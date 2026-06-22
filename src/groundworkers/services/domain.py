"""DomainService — LLM-assisted OMOP domain classification for data dictionary attributes.

Accepts a batch of field labels and their example response values, and returns a
mapping of label → OMOP domain string for any label that can be confidently
classified.  Labels that yield null or an unrecognised domain are omitted so that
callers fall through to the next resolution tier (keyword heuristics).

Valid domains returned: Measurement, Condition, Observation, Procedure, Drug,
Device, Metadata, Identifier.

"Metadata" and "Identifier" are groundcrew-internal sentinel values, not OMOP
CDM domains.  The ingester treats them as skip signals and does not create
SourceItems for those rows.
"""
from __future__ import annotations

from groundworkers.adapters.llm import LLMAdapter
from groundworkers.base.errors import GroundworkersError

_VALID_DOMAINS = frozenset(
    {
        "Measurement",
        "Condition",
        "Observation",
        "Procedure",
        "Drug",
        "Device",
        "Metadata",
        "Identifier",
    }
)

_DOMAIN_SYSTEM = """\
You are a clinical data analyst assigning domains to data dictionary fields.
Given field labels and their possible response values, classify each label into the
most appropriate domain.
Respond with raw JSON only — no markdown fences, no prose.

Valid domains:
  Measurement  — quantitative observations or test results (lab values, vitals, scores, indices)
  Condition    — diagnoses, diseases, disorders, symptoms, or clinical findings
  Observation  — qualitative assessments, social history, behavioural, or lifestyle factors
  Procedure    — clinical interventions, surgeries, therapies, or diagnostic actions
  Drug         — medications, substances, or pharmaceutical agents
  Device       — medical devices, equipment, or implants
  Metadata     — administrative or operational data describing the data collection process
                 rather than the subject's clinical state: form completion fields, examiner
                 or staff initials or names, data entry timestamps, site or facility codes,
                 form version numbers, quality flags, or any field that tracks how or by
                 whom data was recorded
  Identifier   — fields whose sole purpose is to uniquely label a record or entity:
                 participant IDs, subject numbers, case numbers, medical record numbers,
                 study enrolment IDs, or any opaque token used only for linkage

Use null when a label cannot be confidently classified into a single domain.
"""

_DOMAIN_RESPONSE_SCHEMA: dict = {
    "type": "object",
    "description": "Mapping from field label to domain string, or null when uncertain.",
    "additionalProperties": {
        "oneOf": [
            {
                "type": "string",
                "enum": sorted(_VALID_DOMAINS),
            },
            {"type": "null"},
        ]
    },
}


class DomainService:
    """Classify data dictionary field labels into OMOP CDM domains via the LLM adapter."""

    def __init__(self, llm: LLMAdapter) -> None:
        self._llm = llm

    def classify_attributes(
        self,
        label_values: dict[str, list[str]],
        model_name: str | None = None,
    ) -> dict[str, str]:
        """Classify field labels into OMOP domains.

        *label_values* maps each field label text to a (possibly empty) list of
        example response-value strings.  Returns a dict containing only labels
        that received a valid domain string — labels mapped to null or an
        unrecognised value are excluded so callers fall through to the next tier.

        Raises ``BACKEND_UNAVAIL`` when the LLM cannot be reached.
        Raises ``QUERY_ERROR`` when the response is not valid JSON.
        """
        if not label_values:
            return {}

        lines: list[str] = ["Classify each field into the most appropriate OMOP CDM domain."]
        lines.append('Return a JSON object: {"<label>": "<domain or null>", ...}\n')
        for i, (label, values) in enumerate(label_values.items(), 1):
            lines.append(f'{i}. Label: "{label}"')
            if values:
                lines.append(f'   Values: {" | ".join(values[:10])}')
        prompt = "\n".join(lines)

        raw = self._llm.complete_structured(
            prompt,
            _DOMAIN_RESPONSE_SCHEMA,
            system_prompt=_DOMAIN_SYSTEM,
            model_name=model_name,
        )

        return {
            label: domain
            for label, domain in raw.items()
            if isinstance(domain, str) and domain in _VALID_DOMAINS
        }
