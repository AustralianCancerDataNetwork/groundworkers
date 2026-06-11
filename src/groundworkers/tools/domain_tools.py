from __future__ import annotations

from typing import Any

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundcrewServer
from groundworkers.services.domain import DomainService


def register_domain_tools(server: GroundcrewServer, domain_service: DomainService) -> None:
    @server.tool("domain_classify")
    def domain_classify(
        label_values: dict[str, list[str]],
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Classify data dictionary field labels into OMOP CDM domains.

        Accepts *label_values*: a mapping of field label text to a list of example
        response-value strings for that field (empty list when no values are known).

        Returns ``{"classifications": {"<label>": "<domain>", ...}}`` containing only
        labels that received a confident domain assignment.  Labels that could not be
        classified are omitted so callers can fall through to the next resolution tier.

        Valid domains: Measurement, Condition, Observation, Procedure, Drug, Device.

        Returns ``BACKEND_UNAVAIL`` when the LLM is not reachable.
        """
        try:
            result = domain_service.classify_attributes(label_values, model_name=model_name)
            return {"classifications": result}
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}
