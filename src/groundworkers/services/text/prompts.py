from __future__ import annotations

import json
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
    "mapping_cleanup": (
        "You are a clinical terminology expert preparing source text for OMOP vocabulary grounding. "
        "Rewrite the source text into the single best search phrase for downstream OMOP mapping while preserving meaning. "
        "Use any supplied context such as parent label, sibling values, child values, domain hints, and source codes. "
        "Remove scoring syntax, code prefixes, duplicated label fragments, and formatting noise only when they do not carry essential meaning. "
        "Do not invent facts or broaden/narrow the concept beyond what the source and context support. "
        "If the original text is already the best search phrase, return it unchanged."
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
    if operation == "mapping_cleanup":
        context = kwargs.get("context") or {}
        return (
            "Rewrite the following source item into the single best OMOP search phrase.\n"
            f"Original text: {text!r}\n"
            f"Domain hint: {domain_hint or 'not specified'}\n"
            f"Context: {json.dumps(context, ensure_ascii=True, sort_keys=True)}\n"
            "Return JSON with: replacement, original, changed, confidence, notes."
        )
    raise ValueError(f"Unknown operation: {operation!r}")
