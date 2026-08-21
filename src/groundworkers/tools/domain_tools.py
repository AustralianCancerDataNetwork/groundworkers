from __future__ import annotations

from typing import Any

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.services.domain import DomainService


def register_domain_tools(server: GroundworkersMCPServer, domain_service: DomainService) -> None:
    @server.tool("domain_classify")
    async def domain_classify(
        label_values: dict[str, list[str]],
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Classify data dictionary field labels into OMOP CDM domains.

        Accepts *label_values*: a mapping of field label text to a list of example
        response-value strings for that field (empty list when no values are known).

        Returns ``{"classifications": {"<label>": "<domain>", ...}}`` containing only
        labels that received a confident domain assignment.  Labels that could not be
        classified are omitted so callers can fall through to the next resolution tier.

        Valid domains: Measurement, Condition, Observation, Procedure, Drug, Device,
        Metadata, Identifier.

        "Metadata" signals administrative/operational fields (examiner initials, site
        codes, form completion data, data-entry timestamps) that describe the data
        collection process rather than the subject's clinical state.
        "Identifier" signals fields whose sole purpose is unique record linkage
        (participant IDs, case numbers, medical record numbers).
        Groundcrew treats both as skip signals and will not create SourceItems for them.

        Returns ``BACKEND_UNAVAIL`` when the LLM is not reachable.
        """
        try:
            result = await domain_service.async_classify_attributes(
                label_values,
                model_name=model_name,
            )
            return {"classifications": result}
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()
