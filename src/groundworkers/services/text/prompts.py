from __future__ import annotations

from typing import Any


SYSTEM_PROMPTS: dict[str, str] = {
    "normalize": (
        "You are a clinical terminology expert for OMOP (Observational Medical Outcomes Partnership) vocabularies. "
        "Given a clinical term, abbreviation, informal phrase, or misspelling, return the most likely standard "
        "clinical equivalent that would appear in an OMOP vocabulary such as SNOMED CT, RxNorm, LOINC, or ICD. "
        "Expand abbreviations, normalise lay language to clinical terminology, and correct spelling. "
        "Prefer the precise, unambiguous form that a clinician would recognise and that appears in controlled vocabularies."
    ),
    "decompose": (
        "You are a clinical terminology expert for OMOP (Observational Medical Outcomes Partnership) vocabularies. "
        "Given a free-text clinical description, extract and normalise each distinct clinical concept mentioned. "
        "Return only concepts that map to OMOP vocabulary entries: conditions, drugs, measurements, procedures, or observations. "
        "Normalise each concept to its standard clinical name. Avoid duplicating closely related concepts."
    ),
    "disambiguate": (
        "You are a clinical terminology expert for OMOP (Observational Medical Outcomes Partnership) vocabularies. "
        "Given a clinical term or abbreviation, list all plausible clinical interpretations in standard OMOP terminology. "
        "Order interpretations from most to least common in clinical research contexts. "
        "If the term has only one plausible clinical meaning, return a single interpretation and set is_ambiguous to false."
    ),
}


def build_user_prompt(operation: str, text: str, **kwargs: Any) -> str:
    """Construct the user-turn prompt for a text operation.

    Shared between TextService methods and MCP prompt handlers so both present
    the same request surface to the LLM.

    Clamping is applied here so prompt handlers and service calls always show
    the same bounded values.
    """

    domain_hint = kwargs.get("domain_hint") or None
    if operation == "normalize":
        return (
            f"Normalize the following to a standard OMOP clinical term.\n"
            f"Term: {text!r}\n"
            f"Domain hint: {domain_hint or 'not specified'}"
        )
    if operation == "decompose":
        safe_max = max(1, min(int(kwargs.get("max_terms", 10)), 20))
        return (
            f"Extract and normalize clinical concepts from the following text.\n"
            f"Return at most {safe_max} terms.\n"
            f"Text: {text!r}\n"
            f"Domain hint: {domain_hint or 'not specified'}"
        )
    if operation == "disambiguate":
        safe_max = max(1, min(int(kwargs.get("max_interpretations", 5)), 10))
        return (
            f"List all plausible clinical interpretations of the following term.\n"
            f"Return at most {safe_max} interpretations.\n"
            f"Term: {text!r}\n"
            f"Domain hint: {domain_hint or 'not specified'}"
        )
    raise ValueError(f"Unknown operation: {operation!r}")
