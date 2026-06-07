from __future__ import annotations

from typing import Literal, TypeVar

from pydantic import BaseModel, ValidationError

_T = TypeVar("_T", bound=BaseModel)

from groundworkers.adapters.llm import LLMAdapter
from groundworkers.base.errors import GroundworkersError


# ---------------------------------------------------------------------------
# Return types (public service API)
# ---------------------------------------------------------------------------

class NormalizeResult(BaseModel):
    """Result of a single-term normalization."""
    normalized: str
    original: str
    confidence: Literal["high", "medium", "low"]
    notes: str | None = None


class DecomposeTerm(BaseModel):
    """One extracted clinical concept from a decomposition."""
    term: str
    domain_hint: str | None = None


class DecomposeResult(BaseModel):
    """Result of decomposing free text into a list of clinical search terms."""
    terms: list[DecomposeTerm]
    original: str


class Interpretation(BaseModel):
    """One candidate interpretation of an ambiguous term."""
    interpretation: str
    domain_hint: str | None = None
    context_clues: str | None = None


class DisambiguateResult(BaseModel):
    """Result of listing all plausible interpretations of an ambiguous term."""
    interpretations: list[Interpretation]
    original: str
    is_ambiguous: bool


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPTS: dict[str, str] = {
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


# ---------------------------------------------------------------------------
# User-prompt builder
# ---------------------------------------------------------------------------

def _build_user_prompt(operation: str, text: str, **kwargs: object) -> str:
    """Construct the user-turn prompt for the given operation.

    Shared between TextService methods and MCP prompt handlers so both always
    present the same prompt to the LLM.

    Clamping (max_terms 1–20, max_interpretations 1–10) is applied here so
    the MCP prompt reflects the same bounds as the service call.
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


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class TextService:
    """Direct Python API for LLM-backed clinical text preprocessing.

    Normalizes clinical terms, decomposes free-text descriptions into
    individual search terms, and disambiguates ambiguous abbreviations —
    each returning a typed result ready to feed into concept grounding tools.

    All methods raise ValueError for invalid input, GroundworkersError for
    LLM backend failures or malformed LLM responses.
    """

    def __init__(self, llm_adapter: LLMAdapter) -> None:
        self._llm = llm_adapter

    def normalize(
        self,
        text: str,
        *,
        domain_hint: str | None = None,
        model_name: str | None = None,
    ) -> NormalizeResult:
        """Normalize a clinical term, abbreviation, lay phrase, or misspelling.

        Returns the single most likely OMOP-compatible clinical equivalent.
        Raises ValueError if text is empty.
        Raises GroundworkersError on LLM failure or a response that does not
        match the expected structure.
        """
        if not text.strip():
            raise ValueError("text must be a non-empty string")
        prompt = _build_user_prompt("normalize", text, domain_hint=domain_hint)
        return self._call(prompt, NormalizeResult, "normalize", model_name)

    def decompose(
        self,
        text: str,
        *,
        domain_hint: str | None = None,
        max_terms: int = 10,
        model_name: str | None = None,
    ) -> DecomposeResult:
        """Decompose a free-text clinical description into normalized search terms.

        max_terms is clamped to 1–20 before the LLM is called.
        Raises ValueError if text is empty.
        Raises GroundworkersError on LLM failure or a response that does not
        match the expected structure.
        """
        if not text.strip():
            raise ValueError("text must be a non-empty string")
        prompt = _build_user_prompt("decompose", text, domain_hint=domain_hint, max_terms=max_terms)
        return self._call(prompt, DecomposeResult, "decompose", model_name)

    def disambiguate(
        self,
        text: str,
        *,
        domain_hint: str | None = None,
        max_interpretations: int = 5,
        model_name: str | None = None,
    ) -> DisambiguateResult:
        """List all plausible clinical interpretations of an ambiguous term.

        max_interpretations is clamped to 1–10 before the LLM is called.
        Raises ValueError if text is empty.
        Raises GroundworkersError on LLM failure or a response that does not
        match the expected structure.
        """
        if not text.strip():
            raise ValueError("text must be a non-empty string")
        prompt = _build_user_prompt("disambiguate", text, domain_hint=domain_hint, max_interpretations=max_interpretations)
        return self._call(prompt, DisambiguateResult, "disambiguate", model_name)

    def _call(self, prompt: str, model_cls: type[_T], prompt_key: str, model_name: str | None) -> _T:
        raw = self._llm.complete_structured(
            prompt,
            model_cls.model_json_schema(),
            system_prompt=_SYSTEM_PROMPTS[prompt_key],
            model_name=model_name,
        )
        try:
            return model_cls.model_validate(raw)
        except ValidationError as exc:
            raise GroundworkersError(
                "QUERY_ERROR",
                f"LLM response did not match expected structure: {exc}",
            ) from exc
