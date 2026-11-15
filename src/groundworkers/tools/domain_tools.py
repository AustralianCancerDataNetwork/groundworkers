from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.services.domain import DomainService


def register_domain_tools(
    server: GroundworkersMCPServer, domain_service: DomainService
) -> None:
    @server.tool("domain_classify")
    async def domain_classify(
        label_values: Annotated[
            dict[str, list[str]],
            Field(
                description="Map of field label to example values; the first 10 per label are used."
            ),
        ],
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Model-based guess of where data-dictionary fields belong: Measurement, Condition,
        Observation, Procedure, Drug, Device, or the non-OMOP labels Metadata (administrative
        fields such as site codes or form dates) and Identifier (record-linkage IDs). Uses each
        label and its first 10 example values. Unassigned labels are omitted; there is no
        confidence score."""
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
