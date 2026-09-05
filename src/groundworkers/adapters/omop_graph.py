from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from omop_alchemy.cdm.model import normalised_flag
from omop_alchemy.cdm.model.vocabulary import (
    Concept,
    Concept_Class,
    Domain,
    Vocabulary,
)
from omop_graph.extensions.omop_alchemy import PredicateKind
from omop_graph.graph.kg import KnowledgeGraph, KnowledgeGraphEmbeddingConfiguration
from omop_graph.graph.nodes import LabelMatchKind
from omop_graph.graph.paths import find_shortest_paths_batch
from omop_graph.graph.traverse import traverse
from omop_graph.reasoning.grounding import GroundingConstraints, ground_term
from omop_graph.reasoning.resolvers import ResolverPipeline
from omop_graph.reasoning.resolvers.resolvers import EmbeddingResolver
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import NoResultFound

from groundworkers.base.concept_payload import serialise_concept_view
from groundworkers.base.domain_names import DomainNameResolver
from groundworkers.base.errors import GroundworkersError

if TYPE_CHECKING:
    import numpy as np
    from oa_configurator import ResolvedModel
    from omop_emb import EmbeddingBackend
    from omop_llm import ModelBackend

logger = logging.getLogger(__name__)


class EmbeddingTierUnavailable(GroundworkersError):
    """The embedding tier could not run; lexical tiers remain usable.

    Raised instead of failing a whole grounding request so ``GraphService`` can skip
    the embedding tier and continue down the tier plan.
    """

    def __init__(self, message: str) -> None:
        super().__init__("BACKEND_UNAVAIL", message)


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
        embedding_backend_factory: Callable[[], EmbeddingBackend] | None = None,
        resolved_embedding_model: ResolvedModel | None = None,
        model_backend_factory: Callable[[], ModelBackend] | None = None,
        embedding_metric: str = "cosine",
        faiss_cache_dir: str | None = None,
    ) -> None:
        # No `vocab_schema` parameter. It cannot be honoured and never was: no
        # omop-alchemy model declares `schema="vocab"`, so oa-configurator's
        # "vocab" translate-map key is unreachable from the ORM and vocabulary
        # tables resolve through the primary schema like every other CDM table
        # (see omop_alchemy/config.py, `vocabulary_identity`). The engine handed
        # in already carries the full map, so if omop-alchemy ever tags its
        # vocabulary models the routing starts working here with no change.
        # Re-adding the parameter would restore a caller-visible argument that
        # silently changes nothing.
        self.engine = engine
        self._embedding_backend_factory = embedding_backend_factory
        self._resolved_embedding_model = resolved_embedding_model
        self._model_backend_factory = model_backend_factory
        self._embedding_metric = embedding_metric
        self._faiss_cache_dir = faiss_cache_dir
        self._embedding_configuration_error: str | None = None
        self._embedding_configured = False
        self._kg: KnowledgeGraph | None = None
        self._model_backend: ModelBackend | None = None
        self._domain_names = DomainNameResolver(engine)

    @property
    def embedding_resolver_active(self) -> bool:
        """Whether the embedding grounding tier can actually produce candidates.

        Requires all three inputs, not just a valid store configuration: the vector
        store backend, the resolved model, and a callable model backend to encode the
        query. The read-oriented server builds the graph with ``write=False``, and
        omop-graph derives its on-demand query encoder from the *writer* interface —
        so without a Groundworkers-supplied encoder the embedding resolver would run
        and return nothing. Reporting active in that state is the silent-degradation
        failure this property exists to prevent.
        """

        return self._embedding_configured and self._model_backend_factory is not None

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
            return True, self._embedding_configuration_error
        except GroundworkersError as exc:
            return False, exc.message
        except Exception as exc:
            return False, f"Graph probe failed with {type(exc).__name__}."

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
        direction: Literal["in", "out"],
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
                # EdgeView has no normalized activity property, so apply
                # omop-alchemy's canonical flag semantics directly.
                "valid": normalised_flag(edge.invalid_reason) is None,
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
    _PREDICATE_KIND_NAMES: ClassVar[dict[str, PredicateKind]] = {
        pk.name.upper(): pk for pk in PredicateKind
    }

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

        Raises ``EmbeddingTierUnavailable`` when an embedding tier cannot be encoded,
        so the caller can fall through to the remaining lexical tiers.
        """
        pipeline = ResolverPipeline(resolvers=resolvers)
        query_embedding = (
            self._encode_query(query) if self._is_embedding_tier(resolvers) else None
        )
        try:
            raw = ground_term(
                pipeline,
                self._get_kg(),
                query,
                query_embedding=query_embedding,
                constraints=constraints,
                max_candidates=limit,
            )
        except Exception as exc:
            raise self._wrap_graph_error(exc, default_code="QUERY_ERROR")
        return [self._serialise_ground_hit(r) for r in raw]

    async def async_run_ground_tier(
        self,
        resolvers: tuple[Any, ...],
        query: str,
        *,
        constraints: GroundingConstraints,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Run a grounding tier with native async query encoding when needed."""

        pipeline = ResolverPipeline(resolvers=resolvers)
        query_embedding = (
            await self._async_encode_query(query)
            if self._is_embedding_tier(resolvers)
            else None
        )
        try:
            raw = ground_term(
                pipeline,
                self._get_kg(),
                query,
                query_embedding=query_embedding,
                constraints=constraints,
                max_candidates=limit,
            )
        except Exception as exc:
            raise self._wrap_graph_error(exc, default_code="QUERY_ERROR")
        return [self._serialise_ground_hit(r) for r in raw]

    @staticmethod
    def _is_embedding_tier(resolvers: tuple[Any, ...]) -> bool:
        return any(isinstance(resolver, EmbeddingResolver) for resolver in resolvers)

    def _encode_query(self, query: str) -> np.ndarray:
        """Encode the query text with the one configured Groundworkers model.

        The read-oriented graph is built with ``write=False``, and omop-graph's
        on-demand query encoding runs only through a write-capable interface. The
        query vector is therefore supplied explicitly here — a read-only operation
        that never touches the embedding store — using the same ``ModelBackend`` and
        ``EmbeddingRole.QUERY`` role as the embedding tools.
        """
        if self._model_backend_factory is None:
            raise EmbeddingTierUnavailable(
                "The embedding tier needs a configured embedding model to encode the "
                "query. Configure groundworkers.embedding_model_name."
            )
        try:
            from omop_emb import EmbeddingReaderInterface, EmbeddingRole

            if self._model_backend is None:
                self._model_backend = self._model_backend_factory()
            embedding = EmbeddingReaderInterface.generate_embeddings(
                self._model_backend,
                query,
                role=EmbeddingRole.QUERY,
            )
        except Exception as exc:
            # Provider and store errors can carry endpoints or credentials, so report
            # the type only and let the remaining lexical tiers run.
            detail = (
                "The configured embedding model could not encode the query "
                f"({type(exc).__name__}); lexical tiers remain available."
            )
            logger.warning("%s", detail)
            raise EmbeddingTierUnavailable(detail) from exc
        if embedding.shape[0] != 1:
            raise EmbeddingTierUnavailable(
                "The configured embedding model returned an unexpected vector shape "
                f"{tuple(embedding.shape)} for one query."
            )
        return embedding

    async def _async_encode_query(self, query: str) -> np.ndarray:
        if self._model_backend_factory is None:
            raise EmbeddingTierUnavailable(
                "The embedding tier needs a configured embedding model to encode the "
                "query. Configure groundworkers.embedding_model_name."
            )
        try:
            import numpy as np
            from omop_emb import EmbeddingRole

            if self._model_backend is None:
                self._model_backend = self._model_backend_factory()
            vectors = await self._model_backend.async_embed_texts(
                [query],
                role=EmbeddingRole.QUERY,
            )
            embedding = np.asarray(vectors)
        except Exception as exc:
            detail = (
                "The configured embedding model could not encode the query "
                f"({type(exc).__name__}); lexical tiers remain available."
            )
            logger.warning("%s", detail)
            raise EmbeddingTierUnavailable(detail) from exc
        if embedding.ndim != 2 or embedding.shape[0] != 1:
            raise EmbeddingTierUnavailable(
                "The configured embedding model returned an unexpected vector shape "
                f"{tuple(embedding.shape)} for one query."
            )
        return embedding

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

    def canonicalize_domain(self, domain: str | None) -> str | None:
        """Resolve a loosely-typed domain to its canonical ``domain_id``.

        Returns ``None`` for an unrecognised domain so the caller can reject it,
        which is distinct from ``None`` input meaning "no domain constraint".
        Use :meth:`describe_unknown_domain` to build the message.

        This previously matched against a hardcoded six-name tuple — the set
        ``domain_classify`` emits — so the other forty-four domains only worked
        when supplied in exactly the right case.
        """
        return self._domain_names.canonical(domain)

    def known_domains(self) -> tuple[str, ...]:
        """Every valid ``domain_id``, for validation and select lists."""
        return self._domain_names.known_domains()

    def describe_unknown_domain(self, domain: str) -> str:
        return self._domain_names.describe_unknown(domain)

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
            raise GroundworkersError(
                "BACKEND_UNAVAIL",
                f"Cannot connect to database ({type(exc).__name__}).",
            ) from exc

        emb_config: KnowledgeGraphEmbeddingConfiguration | None = None
        if (
            self._embedding_backend_factory is not None
            and self._resolved_embedding_model is not None
        ):
            try:
                from omop_emb import MetricType

                emb_config = KnowledgeGraphEmbeddingConfiguration(
                    metric_type=MetricType(self._embedding_metric),
                    backend=self._embedding_backend_factory(),
                    resolved_model=self._resolved_embedding_model,
                    write=False,
                    compute_missing_embeddings=False,
                    faiss_cache_dir=self._faiss_cache_dir,
                )
                self._embedding_configured = True
            except Exception as exc:
                # Broad except: lexical fallback is intentional.
                emb_config = None
                self._embedding_configured = False
                self._embedding_configuration_error = self._embedding_failure_detail(exc)
                logger.warning("%s", self._embedding_configuration_error)

        try:
            self._kg = KnowledgeGraph(cdm_engine=self.engine, emb_config=emb_config)
        except Exception as exc:
            if emb_config is None:
                self._embedding_configured = False
                raise self._wrap_graph_error(exc, default_code="BACKEND_UNAVAIL")
            # The graph rejected the embedding configuration itself. Keep lexical
            # grounding available rather than failing the whole backend, and report
            # the reason as status detail instead of marking embeddings active.
            self._embedding_configured = False
            self._embedding_configuration_error = self._embedding_failure_detail(exc)
            logger.warning("%s", self._embedding_configuration_error)
            try:
                self._kg = KnowledgeGraph(cdm_engine=self.engine, emb_config=None)
            except Exception as lexical_exc:
                raise self._wrap_graph_error(lexical_exc, default_code="BACKEND_UNAVAIL")
        return self._kg

    @staticmethod
    def _embedding_failure_detail(exc: Exception) -> str:
        """Safe status detail for an embedding-configuration failure.

        Reports the exception type only: provider and database errors can carry
        endpoints, credentials, or connection strings in their messages.
        """
        return (
            "Embedding configuration failed with "
            f"{type(exc).__name__}; lexical grounding remains available."
        )

    # `Any`, not `object`: these are omop-graph view/result objects whose types
    # are not exported, so their attributes cannot be checked statically.
    def _serialise_concept_view(self, concept_view: Any) -> dict[str, Any]:
        """Full-detail concept payload. Shape owned by ``base.concept_payload``."""
        return serialise_concept_view(concept_view, detail="full")

    def _serialise_ground_hit(self, result: Any) -> dict[str, Any]:
        concept_id = int(result.concept_id)
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
            "concept_name": result.concept_name,
            "match_kind": self._label_match_kind_name(result.match_kind),
            "matched_label": getattr(result, "matched_concept_label", None),
            "total_score": round(float(result.total_score), 4),
            "relevance": round(float(getattr(result, "relevance", 0.0)), 4),
            "parsimony_penalty": round(float(getattr(result, "parsimony_penalty", 0.0)), 4),
            "broadness_bonus": round(float(getattr(result, "broadness_bonus", 0.0)), 4),
            "embedding_score": round(float(emb_score), 4) if emb_score is not None else None,
            "separation": int(getattr(result, "separation", 0)),
            "standardized_from": standardized_from,
        }

    # omop-graph's LabelMatchKind member names mapped to Groundworkers' stable
    # public match_kind strings. Keyed by enum member rather than by ordinal value
    # so an upstream reordering cannot silently re-label a tier.
    _MATCH_KIND_NAMES: ClassVar[dict[LabelMatchKind, str]] = {
        LabelMatchKind.EXACT: "EXACT",
        LabelMatchKind.FTS: "FULLTEXT",
        LabelMatchKind.PARTIAL: "PARTIAL",
        LabelMatchKind.EMBEDDING: "EMBEDDING_NEAREST",
    }

    @classmethod
    def _label_match_kind_name(cls, match_kind: object) -> str:
        if isinstance(match_kind, LabelMatchKind):
            return cls._MATCH_KIND_NAMES.get(match_kind, match_kind.name)
        return str(match_kind)

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
        return GroundworkersError(
            default_code,
            f"omop-graph operation failed with {type(exc).__name__}.",
        )
