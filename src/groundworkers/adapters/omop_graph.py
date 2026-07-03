from __future__ import annotations

import logging
from collections.abc import Sequence
from datetime import date
from typing import Any

from omop_graph.extensions.omop_alchemy import PredicateKind
from omop_graph.graph.kg import KnowledgeGraph, KnowledgeGraphEmbeddingConfiguration
from omop_graph.graph.paths import find_shortest_paths_batch
from omop_graph.graph.traverse import traverse
from omop_graph.reasoning.grounding import GroundingConstraints, ground_term
from omop_graph.reasoning.resolvers import ResolverPipeline
from omop_alchemy.cdm.model.vocabulary import (
    Concept,
    Concept_Class,
    Domain,
    Vocabulary,
)
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import NoResultFound

from omop_emb import EmbeddingClient

from groundworkers.base.errors import GroundworkersError

logger = logging.getLogger(__name__)


class OmopGraphAdapter:
    """Dependency-shaped wrapper around the omop-graph backend runtime.

    This adapter owns everything omop-graph specific: the ``KnowledgeGraph``
    lifecycle and embedding configuration, translation of omop-graph/SQLAlchemy
    exceptions into ``GroundworkersError``, and a set of normalized *primitives*
    that each map to roughly one omop-graph operation and return plain dicts /
    tuples (never raw omop-graph objects).

    Multi-step orchestration (hierarchy walks, path assembly, grounding tier
    selection, neighbourhood shaping) lives in ``GraphService``, which composes
    these primitives. Keep this class dependency-shaped: no caller-facing policy.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        vocab_schema: str = "omop_vocab",
        emb_model_name: str | None = None,
        embedding_client: EmbeddingClient | None = None,
    ) -> None:
        self.engine = engine
        self.vocab_schema = vocab_schema
        self.emb_model_name = emb_model_name
        self._embedding_client: EmbeddingClient | None = embedding_client
        self._kg: KnowledgeGraph | None = None

    def set_embedding_client(self, client: EmbeddingClient, model_name: str | None = None) -> None:
        """Configure an EmbeddingClient so the embedding grounding tier can encode queries.

        Safe to call after construction. Any cached KnowledgeGraph is invalidated so the
        embedding-enabled graph configuration is rebuilt on the next request.
        """
        self._embedding_client = client
        if model_name is not None:
            self.emb_model_name = model_name
        self._kg = None

    @property
    def embedding_resolver_active(self) -> bool:
        """True when an EmbeddingClient is configured and the embedding grounding tier is live.

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
        self._kg = None

    # ------------------------------------------------------------------
    # Normalized concept primitives
    # ------------------------------------------------------------------

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

    def concept_views(self, concept_ids: Sequence[int]) -> dict[int, dict[str, Any]]:
        """Batch-fetch normalized concept views keyed by concept_id.

        Returns an empty dict when no ids are supplied or the lookup fails, so
        callers can treat a missing key as "unknown concept" without special-casing
        backend errors during enrichment.
        """
        if not concept_ids:
            return {}
        kg = self._get_kg()
        try:
            views = kg.concept_views(tuple(concept_ids), sort=False)
        except Exception:
            return {}
        return {int(v.concept_id): self._serialise_concept_view(v) for v in views}

    # ------------------------------------------------------------------
    # Hierarchy primitives
    # ------------------------------------------------------------------

    def parents(self, concept_id: int) -> tuple[int, ...]:
        try:
            return tuple(int(p) for p in self._get_kg().parents(concept_id))
        except Exception as exc:
            raise self._wrap_graph_error(exc, default_code="QUERY_ERROR")

    def children(self, concept_id: int) -> tuple[int, ...]:
        try:
            return tuple(int(c) for c in self._get_kg().children(concept_id))
        except Exception as exc:
            raise self._wrap_graph_error(exc, default_code="QUERY_ERROR")

    # ------------------------------------------------------------------
    # Edge primitive
    # ------------------------------------------------------------------

    def edges(
        self,
        concept_id: int,
        *,
        direction: str,
        predicate_kinds: frozenset[PredicateKind] | None = None,
        active_only: bool,
    ) -> list[dict[str, Any]]:
        """Return normalized edges for one concept (no concept-name enrichment).

        Each edge: ``{subject_id, object_id, predicate_id, predicate_kind, valid}``.
        """
        try:
            raw = self._get_kg().edges(
                concept_id,
                direction=direction,
                predicate_kinds=predicate_kinds,
                active_only=active_only,
            )
        except Exception as exc:
            raise self._wrap_graph_error(exc, default_code="QUERY_ERROR")
        return [
            {
                "subject_id": int(edge.subject_id),
                "object_id": int(edge.object_id),
                "predicate_id": edge.predicate_id,
                "predicate_kind": edge.predicate_kind.name,
                "valid": edge.invalid_reason is None,
            }
            for edge in raw
        ]

    # ------------------------------------------------------------------
    # Path primitive
    # ------------------------------------------------------------------

    def shortest_paths(
        self,
        source_id: int,
        target_id: int,
        *,
        max_depth: int,
        predicate_kinds: frozenset[PredicateKind] | None = None,
        within_domain: bool,
    ) -> list[list[dict[str, Any]]]:
        """Return shortest paths as lists of normalized steps (no name enrichment).

        Each step: ``{subject_id, object_id, predicate, predicate_kind}``.
        """
        kg = self._get_kg()
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

        serialised: list[list[dict[str, Any]]] = []
        for path in paths:
            steps: list[dict[str, Any]] = []
            for step in path.steps:
                try:
                    pred_kind = kg.predicate_kind(step.predicate).name
                except Exception:
                    pred_kind = "UNKNOWN"
                steps.append(
                    {
                        "subject_id": int(step.subject.concept_id),
                        "object_id": int(step.object.concept_id),
                        "predicate": step.predicate,
                        "predicate_kind": pred_kind,
                    }
                )
            serialised.append(steps)
        return serialised

    # ------------------------------------------------------------------
    # Neighbourhood primitive
    # ------------------------------------------------------------------

    # Valid predicate kind names accepted by traverse_neighborhood (case-insensitive).
    _PREDICATE_KIND_NAMES: dict[str, PredicateKind] = {pk.name.upper(): pk for pk in PredicateKind}

    def traverse_neighborhood(
        self,
        concept_id: int,
        *,
        predicate_kind_names: list[str] | None,
        max_depth: int,
        max_nodes: int,
    ) -> dict[str, Any]:
        """Bounded BFS from a seed concept.

        Returns ``{neighbor_ids, edges, edge_count, terminated_reason}`` where
        ``edges`` are normalized (``{subject_id, predicate_id, predicate_kind, object_id}``)
        and ``neighbor_ids`` excludes the seed. Raises INVALID_INPUT for an unknown
        predicate-kind name.
        """
        pk_set: set[PredicateKind] | None = None
        if predicate_kind_names is not None:
            pk_set = set()
            for pk_name in predicate_kind_names:
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
                kg=self._get_kg(),
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
        edges = [
            {
                "subject_id": int(edge.subject_id),
                "predicate_id": edge.predicate_id,
                "predicate_kind": edge.predicate_kind.name,
                "object_id": int(edge.object_id),
            }
            for edge in subgraph.edges
        ]
        return {
            "neighbor_ids": neighbor_ids,
            "edges": edges,
            "edge_count": len(subgraph.edges),
            "terminated_reason": graph_trace.terminated_reason if graph_trace else None,
        }

    # ------------------------------------------------------------------
    # Grounding primitive
    # ------------------------------------------------------------------

    def run_ground_tier(
        self,
        resolvers: tuple[Any, ...],
        query: str,
        *,
        constraints: GroundingConstraints,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Run one resolver tier through omop-graph and normalize the hits.

        Returns ground hits without concept-view enrichment; ``GraphService`` adds
        vocabulary/domain/class fields and applies tier-selection policy.
        """
        pipeline = ResolverPipeline(resolvers=resolvers)
        try:
            raw = ground_term(
                pipeline,
                self._get_kg(),
                query,
                query_embedding=None,  # embedding is handled by the KG via its emb_config
                constraints=constraints,
                max_candidates=limit,
            )
        except Exception as exc:
            raise self._wrap_graph_error(exc, default_code="QUERY_ERROR")
        return [self._serialise_ground_hit(r) for r in raw]

    # ------------------------------------------------------------------
    # Vocabulary catalogue (raw backend query)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Domain normalization
    # ------------------------------------------------------------------

    _KNOWN_DOMAIN_NAMES: tuple[str, ...] = (
        "Condition",
        "Procedure",
        "Drug",
        "Measurement",
        "Device",
        "Observation",
    )

    def canonicalize_domain(self, domain: str | None) -> str | None:
        if domain is None:
            return None
        domain_lower = domain.lower()
        return next((k for k in self._KNOWN_DOMAIN_NAMES if k.lower() == domain_lower), domain)

    # ------------------------------------------------------------------
    # Internals — KG lifecycle, normalization, exception translation
    # ------------------------------------------------------------------

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

    def _serialise_ground_hit(self, result: object) -> dict[str, Any]:
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

    @staticmethod
    def _label_match_kind_name(match_kind: object) -> str:
        _MAP = {0: "EXACT", 1: "FULLTEXT", 2: "PARTIAL", 3: "EMBEDDING_NEAREST"}
        val = getattr(match_kind, "value", None)
        if isinstance(val, int):
            return _MAP.get(val, str(match_kind))
        return str(match_kind)

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
