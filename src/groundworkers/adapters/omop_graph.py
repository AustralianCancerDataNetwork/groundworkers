from __future__ import annotations

from collections import deque
from collections.abc import Callable
from datetime import date
from typing import Any

from omop_graph.extensions.omop_alchemy import PredicateKind
from omop_graph.graph.constraints import SearchConstraintConcept
from omop_graph.graph.kg import KnowledgeGraph, KnowledgeGraphEmbeddingConfiguration
from omop_graph.graph.paths import find_shortest_paths_batch
from omop_graph.graph.traverse import traverse
from omop_graph.reasoning.grounding import GroundingConstraints, ground_term
from omop_graph.reasoning.resolvers import ResolverPipeline
from omop_graph.reasoning.resolvers.resolvers import (
    EmbeddingResolver,
    ExactLabelResolver,
    ExactSynonymResolver,
    FullTextResolver,
    FullTextSynonymResolver,
    PartialLabelResolver,
    PartialSynonymResolver,
)
from omop_alchemy.cdm.model.vocabulary import (
    Concept,
    Concept_Ancestor,
    Concept_Class,
    Domain,
    Vocabulary,
)
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import NoResultFound

from omop_emb import EmbeddingClient

from groundworkers.base.errors import GroundworkersError

class OmopGraphAdapter:
    def __init__(
        self,
        engine: Engine,
        *,
        vocab_schema: str = "omop_vocab",
        emb_model_name: str | None = None,
        embedding_client: EmbeddingClient | None = None,
        min_fulltext_overlap: float = 0.0,
    ) -> None:
        self.engine = engine
        self.vocab_schema = vocab_schema
        self.emb_model_name = emb_model_name
        self._embedding_client: EmbeddingClient | None = embedding_client
        self.min_fulltext_overlap = min_fulltext_overlap
        self._kg: KnowledgeGraph | None = None

    def set_embedding_client(self, client: EmbeddingClient, model_name: str | None = None) -> None:
        """Configure an EmbeddingClient so concept_ground can encode query strings on-the-fly.

        Safe to call after construction — the knowledge graph does not need to be rebuilt.
        """
        self._embedding_client = client
        if model_name is not None:
            self.emb_model_name = model_name

    @property
    def embedding_resolver_active(self) -> bool:
        """True when an EmbeddingClient is configured and the embedding tier in concept_ground is live.

        Independent from OmopEmbAdapter.is_available() — both must be True to confirm
        the full embedding pipeline is operational.
        """
        return self._embedding_client is not None

    def is_available(self) -> bool:
        try:
            self._get_kg()
            return True
        except GroundworkersError:
            return False

    def probe(self) -> tuple[bool, str | None]:
        """Return (available, detail) without raising."""
        try:
            self._get_kg()
            return True, None
        except GroundworkersError as exc:
            return False, exc.message
        except Exception as exc:
            return False, repr(exc)

    def close(self) -> None:
        self.engine.dispose()
        self._kg = None

    def get_concept(self, concept_id: int) -> dict[str, Any] | None:
        try:
            concept_view = self._get_kg().concept_view(concept_id)
        except Exception as exc:
            if self._is_not_found(exc):
                return None
            raise self._wrap_graph_error(exc, default_code="QUERY_ERROR")
        return self._serialise_concept_view(concept_view)

    def get_concept_by_code(self, vocabulary_id: str, code: str) -> list[dict[str, Any]]:
        try:
            concept_id = self._get_kg().concept_id_by_code(vocabulary_id, code)
            concept_view = self._get_kg().concept_view(concept_id)
        except Exception as exc:
            if self._is_not_found(exc):
                return []
            raise self._wrap_graph_error(exc, default_code="QUERY_ERROR")
        return [self._serialise_concept_view(concept_view)]

    def get_ancestors(self, concept_id: int, max_depth: int) -> list[dict[str, Any]]:
        kg = self._get_kg()
        if self.get_concept(concept_id) is None:
            raise GroundworkersError("NOT_FOUND", f"Concept {concept_id} was not found")

        queue: deque[tuple[int, int]] = deque((parent_id, 1) for parent_id in kg.parents(concept_id))
        return self._walk_hierarchy(queue=queue, neighbour_getter=kg.parents, max_depth=max_depth)

    def ground(
        self,
        query: str,
        limit: int,
        domain: str | None,
        vocabulary_id: str | None,
        parent_ids: tuple[int, ...] | None = None,
    ) -> dict[str, Any]:
        """Ground free text to ranked standard OMOP concepts.

        Returns a dict with keys:
          results              — ranked list of grounded concepts with scoring fields
          grounding_explanation — summary of which tier matched and what constraints ran
        """
        kg = self._get_kg()

        # Normalise domain to its canonical OMOP casing (e.g. "condition" → "Condition").
        # OMOP domain_id values are title-cased; a case-insensitive match against the
        # known root codes table handles the common mistake of passing lowercase names.
        if domain is not None:
            _domain_lower = domain.lower()
            domain = next(
                (k for k in self._DOMAIN_ROOT_CODES if k.lower() == _domain_lower),
                domain,  # unknown domain: pass through unchanged
            )

        search_constraint = None
        if domain or vocabulary_id:
            search_constraint = SearchConstraintConcept(
                domains=(domain,) if domain else None,
                vocabularies=(vocabulary_id,) if vocabulary_id else None,
            )

        if parent_ids is not None:
            resolved_parent_ids: tuple[int, ...] = parent_ids
            parent_ids_source = "explicit"
        elif domain is not None:
            resolved_parent_ids = self._get_domain_root_ids(domain)
            parent_ids_source = "domain_root"
        else:
            # No domain filter: collect roots across all known domains so hierarchy
            # anchoring doesn't silently drop every candidate.
            all_roots: list[int] = []
            for d in self._DOMAIN_ROOT_CODES:
                all_roots.extend(self._get_domain_root_ids(d))
            resolved_parent_ids = tuple(all_roots)
            parent_ids_source = "all_domain_roots"

        if not resolved_parent_ids:
            raise GroundworkersError(
                "QUERY_ERROR",
                "No hierarchy anchors found — ensure the OMOP vocabulary is bootstrapped "
                "(concept and concept_ancestor tables must be populated).",
            )

        constraints = GroundingConstraints(parent_ids=resolved_parent_ids, search_constraint=search_constraint)

        tiers: list[tuple[Any, ...]] = [
            (ExactLabelResolver(), ExactSynonymResolver()),
            (FullTextResolver(), FullTextSynonymResolver()),
        ]
        if self.emb_model_name or self._embedding_client is not None:
            tiers.append((EmbeddingResolver(),))
        tiers.append((PartialLabelResolver(), PartialSynonymResolver()))

        results: list[Any] = []
        for tier in tiers:
            is_fts_tier = any(
                isinstance(r, (FullTextResolver, FullTextSynonymResolver)) for r in tier
            )
            pipeline = ResolverPipeline(resolvers=tier)
            try:
                raw = ground_term(
                    pipeline, kg, query,
                    query_embedding=None,  # embedding is handled by the KG via its emb_config
                    constraints=constraints,
                    max_candidates=limit,
                )
            except Exception as exc:
                raise self._wrap_graph_error(exc, default_code="QUERY_ERROR")
            # Drop FTS hits where fewer than min_fulltext_overlap of the query tokens appear
            # in the matched concept name, then fall through to a higher-quality tier.
            if raw and is_fts_tier and self.min_fulltext_overlap > 0.0:
                query_tokens = set(query.lower().split())
                filtered = [
                    r for r in raw
                    if self._fts_overlap(query_tokens, r.matched_concept_label or "")
                    >= self.min_fulltext_overlap
                ]
                results = filtered
            else:
                results = list(raw)
            if results:
                break

        concept_ids = tuple(dict.fromkeys(r.concept_id for r in results))
        try:
            views = {v.concept_id: v for v in kg.concept_views(concept_ids, sort=False)} if concept_ids else {}
        except Exception:
            views = {}

        matched_tier = self._label_match_kind_name(results[0].match_kind) if results else None
        used_embedding = any(getattr(r, "embedding_score", None) is not None for r in results)

        return {
            "results": [self._serialise_ground_result(r, views) for r in results],
            "grounding_explanation": {
                "matched_tier": matched_tier,
                "used_embedding": used_embedding,
                "effective_parent_ids": list(resolved_parent_ids),
                "parent_ids_source": parent_ids_source,
            },
        }

    # Valid predicate kind names accepted by get_neighbors (case-insensitive).
    _PREDICATE_KIND_NAMES: dict[str, PredicateKind] = {pk.name.upper(): pk for pk in PredicateKind}

    def get_neighbors(
        self,
        concept_id: int,
        max_depth: int,
        predicate_kinds: list[str] | None,
        max_nodes: int,
        include_edges: bool,
    ) -> dict[str, Any]:
        """Bounded multi-hop neighborhood exploration via BFS.

        Follows outgoing relationship edges from the seed concept up to
        max_depth hops, collecting all reachable concepts and (optionally)
        the edges that connect them.
        """
        kg = self._get_kg()
        if self.get_concept(concept_id) is None:
            raise GroundworkersError("NOT_FOUND", f"Concept {concept_id} was not found")

        pk_set: set[PredicateKind] | None = None
        if predicate_kinds is not None:
            pk_set = set()
            for pk_name in predicate_kinds:
                key = pk_name.upper()
                if key not in self._PREDICATE_KIND_NAMES:
                    valid = sorted(self._PREDICATE_KIND_NAMES)
                    raise GroundworkersError(
                        "INVALID_INPUT",
                        f"Unknown predicate_kind {pk_name!r}. Valid values: {valid}",
                    )
                pk_set.add(self._PREDICATE_KIND_NAMES[key])

        try:
            subgraph, graph_trace = traverse(
                kg=kg,
                seeds=(concept_id,),
                predicate_kinds=pk_set,
                max_depth=max_depth,
                on=None,
                max_nodes=max_nodes,
                trace=True,  # always trace so we can report terminated_reason
            )
        except Exception as exc:
            raise self._wrap_graph_error(exc, default_code="QUERY_ERROR")

        neighbor_ids = tuple(n for n in sorted(subgraph.nodes) if n != concept_id)
        try:
            views = {v.concept_id: v for v in kg.concept_views(neighbor_ids, sort=False)} if neighbor_ids else {}
        except Exception:
            views = {}

        neighbors: list[dict[str, Any]] = []
        for nid in neighbor_ids:
            view = views.get(nid)
            if view:
                neighbors.append({
                    "concept_id": int(view.concept_id),
                    "concept_name": view.concept_name,
                    "vocabulary_id": view.vocabulary_id,
                    "domain_id": view.domain_id,
                    "concept_class_id": view.concept_class_id,
                    "standard_concept": bool(view.standard_concept),
                })

        edges: list[dict[str, Any]] = []
        if include_edges:
            for edge in subgraph.edges:
                edges.append({
                    "subject_id": int(edge.subject_id),
                    "predicate_id": edge.predicate_id,
                    "predicate_kind": edge.predicate_kind.name,
                    "object_id": int(edge.object_id),
                })

        terminated_reason = graph_trace.terminated_reason if graph_trace else None
        return {
            "concept_id": concept_id,
            "neighbor_count": len(neighbors),
            "edge_count": len(subgraph.edges),
            "neighbors": neighbors,
            "edges": edges,
            "terminated_early": terminated_reason is not None,
            "terminated_reason": terminated_reason,
        }

    def get_edges(self, concept_id: int) -> dict[str, Any]:
        kg = self._get_kg()
        if self.get_concept(concept_id) is None:
            raise GroundworkersError("NOT_FOUND", f"Concept {concept_id} was not found")
        try:
            outbound = kg.edges(concept_id, direction="out", active_only=False)
            inbound = kg.edges(concept_id, direction="in", active_only=False)
        except Exception as exc:
            raise self._wrap_graph_error(exc, default_code="QUERY_ERROR")

        other_ids = tuple(dict.fromkeys([e.object_id for e in outbound] + [e.subject_id for e in inbound]))
        try:
            views = {v.concept_id: v for v in kg.concept_views(other_ids, sort=False)} if other_ids else {}
        except Exception:
            views = {}

        return {
            "outbound": [self._serialise_edge_out(e, views) for e in outbound],
            "inbound": [self._serialise_edge_in(e, views) for e in inbound],
        }

    def find_path(
        self,
        source_id: int,
        target_id: int,
        max_depth: int,
        predicate_kinds: frozenset | None = None,
        within_domain: bool = True,
    ) -> dict[str, Any]:
        kg = self._get_kg()
        if self.get_concept(source_id) is None:
            raise GroundworkersError("NOT_FOUND", f"Concept {source_id} was not found")
        if source_id == target_id:
            return {"found": True, "paths": [{"length": 0, "steps": []}]}
        if self.get_concept(target_id) is None:
            raise GroundworkersError("NOT_FOUND", f"Concept {target_id} was not found")

        try:
            paths = find_shortest_paths_batch(
                kg,
                source_id,
                target_id,
                max_depth=max_depth,
                predicate_kinds=predicate_kinds,
                within_domain=within_domain,
            )
        except Exception as exc:
            raise self._wrap_graph_error(exc, default_code="QUERY_ERROR")

        if not paths:
            return {"found": False, "paths": []}

        all_concept_ids: set[int] = set()
        for path in paths:
            for step in path.steps:
                all_concept_ids.add(step.subject.concept_id)
                all_concept_ids.add(step.object.concept_id)
        try:
            views = {v.concept_id: v for v in kg.concept_views(tuple(all_concept_ids), sort=False)} if all_concept_ids else {}
        except Exception:
            views = {}

        serialised: list[dict[str, Any]] = []
        for path in sorted(paths, key=lambda p: len(p.steps)):
            steps = []
            for step in path.steps:
                try:
                    pred_kind = kg.predicate_kind(step.predicate).name
                except Exception:
                    pred_kind = "UNKNOWN"
                subj_view = views.get(step.subject.concept_id)
                obj_view = views.get(step.object.concept_id)
                steps.append({
                    "subject_id": int(step.subject.concept_id),
                    "subject_name": subj_view.concept_name if subj_view else None,
                    "predicate": step.predicate,
                    "predicate_kind": pred_kind,
                    "object_id": int(step.object.concept_id),
                    "object_name": obj_view.concept_name if obj_view else None,
                })
            serialised.append({"length": len(steps), "steps": steps})

        return {"found": True, "paths": serialised}

    # Predicate-kind presets for equivalency path tools.
    _IDENTITY_KINDS: frozenset = frozenset({PredicateKind.IDENTITY})
    _IDENTITY_AND_HIERARCHY_KINDS: frozenset = frozenset({PredicateKind.IDENTITY, PredicateKind.HIERARCHY})

    def find_equivalency_path(
        self,
        source_id: int,
        target_id: int,
        max_depth: int,
        allow_hierarchical_traversal: bool = False,
    ) -> dict[str, Any]:
        """Find paths restricted to identity (and optionally hierarchy) edges.

        When allow_hierarchical_traversal=False only IDENTITY predicates are
        traversed (Maps to, Concept same_as, Concept poss_eq, etc.) — the
        result represents a direct cross-vocabulary equivalence with no loss
        of specificity.

        When allow_hierarchical_traversal=True HIERARCHY predicates (Is a /
        Subsumes) are also allowed.  A path may then step up or down the
        ancestry chain to find a connection, meaning the target may be an
        ancestor of the source — equivalence at a broader level.

        within_domain is always False for equivalency paths: identity
        relationships are designed to cross vocabulary/domain boundaries.
        """
        kinds = self._IDENTITY_AND_HIERARCHY_KINDS if allow_hierarchical_traversal else self._IDENTITY_KINDS
        return self.find_path(
            source_id=source_id,
            target_id=target_id,
            max_depth=max_depth,
            predicate_kinds=kinds,
            within_domain=False,
        )

    def map_to_standard(self, vocabulary_id: str, code: str) -> dict[str, Any]:
        source_list = self.get_concept_by_code(vocabulary_id, code)
        if not source_list:
            raise GroundworkersError("NOT_FOUND", f"Concept {vocabulary_id}:{code} was not found")
        source = source_list[0]

        if source["standard_concept"]:
            return {"source": source, "standard_concepts": [source]}

        kg = self._get_kg()
        try:
            edges = kg.edges(
                source["concept_id"],
                direction="out",
                predicate_kinds=frozenset({PredicateKind.IDENTITY}),
                active_only=True,
            )
        except Exception as exc:
            raise self._wrap_graph_error(exc, default_code="QUERY_ERROR")

        standard_concepts = []
        for edge in edges:
            target = self.get_concept(int(edge.object_id))
            if target and target["standard_concept"]:
                standard_concepts.append(target)

        return {"source": source, "standard_concepts": standard_concepts}

    def get_vocabulary_catalogue(self) -> dict[str, Any]:
        vocab_stmt = (
            select(
                Vocabulary.vocabulary_id,
                Vocabulary.vocabulary_name,
                func.count(Concept.concept_id).label("concept_count"),
            )
            .outerjoin(Concept, Concept.vocabulary_id == Vocabulary.vocabulary_id)
            .group_by(Vocabulary.vocabulary_id, Vocabulary.vocabulary_name)
            .order_by(Vocabulary.vocabulary_id)
        )
        domain_stmt = (
            select(
                Domain.domain_id,
                Domain.domain_name,
                func.count(Concept.concept_id).label("concept_count"),
            )
            .outerjoin(Concept, Concept.domain_id == Domain.domain_id)
            .group_by(Domain.domain_id, Domain.domain_name)
            .order_by(Domain.domain_id)
        )
        class_stmt = (
            select(Concept_Class.concept_class_id, Concept_Class.concept_class_name)
            .order_by(Concept_Class.concept_class_id)
        )
        kg = self._get_kg()
        try:
            with kg.session_factory() as session:
                vocab_rows = session.execute(vocab_stmt).all()
                domain_rows = session.execute(domain_stmt).all()
                class_rows = session.execute(class_stmt).all()
        except Exception as exc:
            raise self._wrap_graph_error(exc, default_code="QUERY_ERROR")

        return {
            "vocabularies": [
                {"vocabulary_id": r[0], "vocabulary_name": r[1], "concept_count": int(r[2])}
                for r in vocab_rows
            ],
            "domains": [
                {"domain_id": r[0], "domain_name": r[1], "concept_count": int(r[2])}
                for r in domain_rows
            ],
            "concept_classes": [
                {"concept_class_id": r[0], "concept_class_name": r[1]}
                for r in class_rows
            ],
        }

    def get_descendants(self, concept_id: int, max_depth: int) -> list[dict[str, Any]]:
        kg = self._get_kg()
        if self.get_concept(concept_id) is None:
            raise GroundworkersError("NOT_FOUND", f"Concept {concept_id} was not found")

        queue: deque[tuple[int, int]] = deque((child_id, 1) for child_id in kg.children(concept_id))
        return self._walk_hierarchy(queue=queue, neighbour_getter=kg.children, max_depth=max_depth)

    def _serialise_ground_result(self, result: object, views: dict) -> dict[str, Any]:
        view = views.get(getattr(result, "concept_id", None))
        concept_id = int(result.concept_id)  # type: ignore[attr-defined]
        original_id = getattr(result, "original_id", None)
        standardized_from = None
        if original_id is not None and int(original_id) != concept_id:
            standardized_from = {
                "concept_id": int(original_id),
                "concept_name": getattr(result, "original_name", None),
            }
        emb_score = getattr(result, "embedding_score", None)
        return {
            "concept_id": concept_id,
            "concept_name": result.concept_name,  # type: ignore[attr-defined]
            "vocabulary_id": view.vocabulary_id if view else None,
            "domain_id": view.domain_id if view else None,
            "concept_class_id": view.concept_class_id if view else None,
            "standard_concept": True,
            "match_kind": self._label_match_kind_name(result.match_kind),  # type: ignore[attr-defined]
            "matched_label": getattr(result, "matched_concept_label", None),
            "total_score": round(float(result.total_score), 4),  # type: ignore[attr-defined]
            "relevance": round(float(getattr(result, "relevance", 0.0)), 4),
            "parsimony_penalty": round(float(getattr(result, "parsimony_penalty", 0.0)), 4),
            "broadness_bonus": round(float(getattr(result, "broadness_bonus", 0.0)), 4),
            "embedding_score": round(float(emb_score), 4) if emb_score is not None else None,
            "separation": int(getattr(result, "separation", 0)),
            "standardized_from": standardized_from,
        }

    def _serialise_edge_out(self, edge: object, views: dict) -> dict[str, Any]:
        view = views.get(int(edge.object_id))  # type: ignore[attr-defined]
        return {
            "relationship_id": edge.predicate_id,  # type: ignore[attr-defined]
            "predicate_kind": edge.predicate_kind.name,  # type: ignore[attr-defined]
            "target_concept_id": int(edge.object_id),  # type: ignore[attr-defined]
            "target_concept_name": view.concept_name if view else None,
            "valid": edge.invalid_reason is None,  # type: ignore[attr-defined]
        }

    def _serialise_edge_in(self, edge: object, views: dict) -> dict[str, Any]:
        view = views.get(int(edge.subject_id))  # type: ignore[attr-defined]
        return {
            "relationship_id": edge.predicate_id,  # type: ignore[attr-defined]
            "predicate_kind": edge.predicate_kind.name,  # type: ignore[attr-defined]
            "source_concept_id": int(edge.subject_id),  # type: ignore[attr-defined]
            "source_concept_name": view.concept_name if view else None,
            "valid": edge.invalid_reason is None,  # type: ignore[attr-defined]
        }

    @staticmethod
    def _fts_overlap(query_tokens: set[str], concept_label: str) -> float:
        if not query_tokens:
            return 1.0
        label_tokens = set(concept_label.lower().split())
        return len(query_tokens & label_tokens) / len(query_tokens)

    @staticmethod
    def _label_match_kind_name(match_kind: object) -> str:
        _MAP = {0: "EXACT", 1: "FULLTEXT", 2: "PARTIAL", 3: "EMBEDDING_NEAREST"}
        val = getattr(match_kind, "value", None)
        if isinstance(val, int):
            return _MAP.get(val, str(match_kind))
        return str(match_kind)

    def _walk_hierarchy(self, *, queue: deque[tuple[int, int]], neighbour_getter: Callable[[int], Any], max_depth: int) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        visited: set[int] = set()
        kg = self._get_kg()

        while queue:
            current_id, depth = queue.popleft()
            if current_id in visited or depth > max_depth:
                continue
            visited.add(current_id)

            try:
                concept_view = kg.concept_view(current_id)
            except Exception as exc:
                if self._is_not_found(exc):
                    continue
                raise self._wrap_graph_error(exc, default_code="QUERY_ERROR")

            results.append(self._serialise_hierarchy_view(concept_view, depth))

            if depth < max_depth:
                try:
                    next_ids = neighbour_getter(current_id)
                except Exception as exc:
                    raise self._wrap_graph_error(exc, default_code="QUERY_ERROR")
                for next_id in next_ids:
                    if next_id not in visited:
                        queue.append((int(next_id), depth + 1))

        results.sort(key=lambda item: (item["depth"], item["concept_id"]))
        return results

    def _get_kg(self) -> KnowledgeGraph:
        if self._kg is not None:
            return self._kg

        # Fail fast with a clear message if the database is unreachable before
        # KnowledgeGraph has a chance to raise something opaque.
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:
            raise GroundworkersError("BACKEND_UNAVAIL", f"Cannot connect to database: {exc}") from exc

        try:
            emb_config: KnowledgeGraphEmbeddingConfiguration | None = None
            if self._embedding_client is not None:
                try:
                    from omop_emb.config import MetricType
                    emb_config = KnowledgeGraphEmbeddingConfiguration(
                        metric_type=MetricType.COSINE,
                        model_name=self.emb_model_name,
                        client=self._embedding_client,
                    )
                except Exception:
                    emb_config = None  # Non-fatal: grounding falls back to non-embedding tiers
            self._kg = KnowledgeGraph(cdm_engine=self.engine, emb_config=emb_config)
        except Exception as exc:
            raise self._wrap_graph_error(exc, default_code="BACKEND_UNAVAIL")
        return self._kg

    # Stable SNOMED concept codes for the top-level concept in each standard OMOP domain.
    # These are consistent across all Athena vocabulary releases (concept_ids may differ
    # between instances, but concept_codes are stable).
    _DOMAIN_ROOT_CODES: dict[str, tuple[str, str]] = {
        "Condition":   ("SNOMED", "404684003"),  # Clinical finding
        "Procedure":   ("SNOMED", "71388002"),   # Procedure
        "Drug":        ("SNOMED", "373873005"),  # Pharmaceutical / biologic product
        "Measurement": ("SNOMED", "363787002"),  # Observable entity
        "Device":      ("SNOMED", "260787004"),  # Physical object
    }

    def _get_domain_root_ids(self, domain: str | None) -> tuple[int, ...]:
        """Return top-level concept IDs to use as hierarchy anchors for the given domain.

        Known domains use a stable SNOMED code lookup (single row). Unknown domains fall
        back to a GROUP BY query over concept_ancestor to find the most-connected root.
        Results are cached per domain.
        """
        if not hasattr(self, "_root_ids_cache"):
            self._root_ids_cache: dict[str, tuple[int, ...]] = {}
        cache_key = domain or ""
        if cache_key in self._root_ids_cache:
            return self._root_ids_cache[cache_key]

        result: tuple[int, ...] = ()
        kg = self._get_kg()

        if domain and domain in self._DOMAIN_ROOT_CODES:
            # Fast path: single-row lookup by the stable SNOMED root concept_code.
            vocab_id, code = self._DOMAIN_ROOT_CODES[domain]
            stmt = (
                select(Concept.concept_id)
                .where(
                    Concept.concept_code == code,
                    Concept.vocabulary_id == vocab_id,
                    Concept.standard_concept == "S",
                )
                .limit(1)
            )
            try:
                with kg.session_factory() as session:
                    rows = session.execute(stmt).all()
                result = tuple(int(r[0]) for r in rows)
            except Exception as exc:
                raise GroundworkersError(
                    "QUERY_ERROR",
                    f"Failed to resolve hierarchy anchors for domain {domain!r}: {exc}",
                ) from exc

        if not result and domain:
            # Fallback for unknown domains, or when the known-code lookup missed.
            # Find the ancestor with the most descendants in this domain — the true
            # root of the hierarchy has the highest descendant count.
            stmt = (
                select(Concept_Ancestor.ancestor_concept_id)
                .join(Concept, Concept.concept_id == Concept_Ancestor.ancestor_concept_id)
                .where(
                    Concept.domain_id == domain,
                    Concept.standard_concept == "S",
                    Concept_Ancestor.min_levels_of_separation > 0,
                )
                .group_by(Concept_Ancestor.ancestor_concept_id)
                .order_by(func.count().desc())
                .limit(3)
            )
            try:
                with kg.session_factory() as session:
                    rows = session.execute(stmt).all()
                result = tuple(int(r[0]) for r in rows)
            except Exception as exc:
                raise GroundworkersError(
                    "QUERY_ERROR",
                    f"Failed to resolve hierarchy anchors for domain {domain!r}: {exc}",
                ) from exc

        self._root_ids_cache[cache_key] = result
        return result

    def _serialise_concept_view(self, concept_view: object) -> dict[str, Any]:
        return {
            "concept_id": int(concept_view.concept_id),  # type: ignore[attr-defined]
            "concept_name": concept_view.concept_name,  # type: ignore[attr-defined]
            "concept_code": concept_view.concept_code,  # type: ignore[attr-defined]
            "vocabulary_id": concept_view.vocabulary_id,  # type: ignore[attr-defined]
            "domain_id": concept_view.domain_id,  # type: ignore[attr-defined]
            "concept_class_id": concept_view.concept_class_id,  # type: ignore[attr-defined]
            "standard_concept": bool(concept_view.standard_concept),  # type: ignore[attr-defined]
            "valid_start_date": self._date_to_iso(concept_view.valid_start_date),  # type: ignore[attr-defined]
            "valid_end_date": self._date_to_iso(concept_view.valid_end_date),  # type: ignore[attr-defined]
            "invalid_reason": concept_view.invalid_reason,  # type: ignore[attr-defined]
        }

    def _serialise_hierarchy_view(self, concept_view: object, depth: int) -> dict[str, Any]:
        return {
            "concept_id": int(concept_view.concept_id),  # type: ignore[attr-defined]
            "concept_name": concept_view.concept_name,  # type: ignore[attr-defined]
            "vocabulary_id": concept_view.vocabulary_id,  # type: ignore[attr-defined]
            "domain_id": concept_view.domain_id,  # type: ignore[attr-defined]
            "standard_concept": bool(concept_view.standard_concept),  # type: ignore[attr-defined]
            "depth": depth,
        }

    @staticmethod
    def _date_to_iso(value: date | str) -> str:
        if isinstance(value, date):
            return value.isoformat()
        return value

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        if isinstance(exc, NoResultFound):
            return True
        return any(cls.__name__ in {"NotFoundError", "ConceptNotFoundError"} for cls in type(exc).__mro__)

    @staticmethod
    def _wrap_graph_error(exc: Exception, *, default_code: str) -> GroundworkersError:
        if isinstance(exc, GroundworkersError):
            return exc
        msg = str(exc)
        if "relationship classification" in msg or "relationship_mapping" in msg:
            return GroundworkersError(
                "BACKEND_UNAVAIL",
                "omop-graph setup incomplete — run: omop-graph relationship-classification",
            )
        return GroundworkersError(default_code, msg or repr(exc))
