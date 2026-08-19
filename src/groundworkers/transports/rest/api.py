from __future__ import annotations

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import JSONResponse

from groundworkers.app import GroundworkersApp
from groundworkers.base.errors import GroundworkersError
from groundworkers.services.source_planning.serialisation import (
    decode_content,
    serialize_pre_ingest_bundle,
)
from groundworkers.transports.rest.models import (
    AssistedPlanRequest,
    AssistedPlanResponse,
    CandidateBundleRequest,
    CandidateBundleResponse,
    ErrorResponse,
    HealthResponse,
)


def create_rest_app(
    application: GroundworkersApp,
    *,
    base_path: str = "/v1",
) -> FastAPI:
    """Create the curated REST transport for groundworkers services."""

    api = FastAPI(
        title=application.config.groundworkers.app_name,
        version="0.1.0",
    )

    @api.exception_handler(GroundworkersError)
    async def handle_groundworkers_error(_, exc: GroundworkersError) -> JSONResponse:
        return JSONResponse(
            status_code=_status_code_for_error(exc.code),
            content=ErrorResponse(code=exc.code, message=exc.message).model_dump(),
        )

    @api.exception_handler(ValueError)
    async def handle_value_error(_, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(code="INVALID_INPUT", message=str(exc)).model_dump(),
        )

    prefix = "" if base_path == "/" else base_path
    router = APIRouter(prefix=prefix, dependencies=[Depends(_rest_auth_dependency)])

    @api.get("/healthz", response_model=HealthResponse)
    def healthz() -> HealthResponse:
        return HealthResponse(status="ok")

    @router.post(
        "/mapping/candidate-bundle",
        response_model=CandidateBundleResponse,
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def mapping_candidate_bundle(request: CandidateBundleRequest) -> CandidateBundleResponse:
        if application.services.mapping is None:
            raise GroundworkersError(
                "BACKEND_UNAVAIL",
                "mapping service is unavailable because the OMOP vocabulary backend is not configured",
            )
        payload = application.services.mapping.concept_candidate_bundle(
            request.query,
            domain=request.domain,
            vocabulary_id=request.vocabulary_id,
            standard_only=request.standard_only,
            active_only=request.active_only,
            include_synonyms=request.include_synonyms,
            include_normalized=request.include_normalized,
            include_fulltext=request.include_fulltext,
            include_embedding=request.include_embedding,
            include_standard_mappings=request.include_standard_mappings,
            include_hierarchy_context=request.include_hierarchy_context,
            include_relationship_summary=request.include_relationship_summary,
            parent_ids=request.parent_ids,
            per_channel_limit=request.per_channel_limit,
            overall_limit=request.overall_limit,
            model_name=request.model_name,
        )
        return CandidateBundleResponse.model_validate(payload)

    @router.post(
        "/source-planning/assisted-plan",
        response_model=AssistedPlanResponse,
        responses={
            400: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def source_planning_assisted_plan(request: AssistedPlanRequest) -> AssistedPlanResponse:
        if application.services.source_planning is None:
            raise GroundworkersError(
                "BACKEND_UNAVAIL",
                "source planning service is unavailable",
            )
        raw_content = decode_content(request.content, request.content_encoding)
        bundle = application.services.source_planning.plan_source_assisted(
            raw_content,
            filename=request.filename,
            caller_hint=request.caller_hint,
        )
        payload = serialize_pre_ingest_bundle(
            bundle,
            include_intermediate=request.include_intermediate,
        )
        return AssistedPlanResponse.model_validate(payload)

    api.include_router(router)
    return api


def _rest_auth_dependency() -> None:
    """Placeholder dependency for future REST authentication."""

    return None


def _status_code_for_error(code: str) -> int:
    return {
        "INVALID_INPUT": 400,
        "FORMAT_BINARY_DECODE": 400,
        "FORMAT_UNRECOGNISED": 400,
        "NOT_FOUND": 404,
        "BACKEND_UNAVAIL": 503,
        "MISSING_DEPENDENCY": 503,
        "QUERY_ERROR": 500,
    }.get(code, 500)
