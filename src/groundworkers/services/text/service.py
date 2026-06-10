from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ValidationError

from groundworkers.adapters.llm import LLMAdapter
from groundworkers.base.errors import GroundworkersError
from groundworkers.services.text.models import (
    DecomposeResult,
    DisambiguateResult,
    NormalizeResult,
)
from groundworkers.services.text.prompts import SYSTEM_PROMPTS, build_user_prompt

_T = TypeVar("_T", bound=BaseModel)


class TextService:
    """Direct Python API for LLM-backed clinical text preprocessing.

    TextService interprets caller-provided clinical phrases. It normalizes
    single terms, decomposes multi-concept free text, and surfaces ranked
    interpretations when the input is ambiguous. The outputs are typed and
    ready to feed into downstream concept grounding workflows.

    All methods raise ValueError for invalid input and GroundworkersError for
    LLM backend failures or malformed structured responses.
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
        """Normalize a clinical term, abbreviation, lay phrase, or misspelling."""

        if not text.strip():
            raise ValueError("text must be a non-empty string")
        prompt = build_user_prompt("normalize", text, domain_hint=domain_hint)
        return self._call(prompt, NormalizeResult, "normalize", model_name)

    def decompose(
        self,
        text: str,
        *,
        domain_hint: str | None = None,
        max_terms: int = 10,
        model_name: str | None = None,
    ) -> DecomposeResult:
        """Decompose a free-text clinical description into normalized search terms."""

        if not text.strip():
            raise ValueError("text must be a non-empty string")
        prompt = build_user_prompt("decompose", text, domain_hint=domain_hint, max_terms=max_terms)
        return self._call(prompt, DecomposeResult, "decompose", model_name)

    def disambiguate(
        self,
        text: str,
        *,
        domain_hint: str | None = None,
        max_interpretations: int = 5,
        model_name: str | None = None,
    ) -> DisambiguateResult:
        """List all plausible clinical interpretations of an ambiguous term."""

        if not text.strip():
            raise ValueError("text must be a non-empty string")
        prompt = build_user_prompt(
            "disambiguate",
            text,
            domain_hint=domain_hint,
            max_interpretations=max_interpretations,
        )
        return self._call(prompt, DisambiguateResult, "disambiguate", model_name)

    def _call(self, prompt: str, model_cls: type[_T], prompt_key: str, model_name: str | None) -> _T:
        raw = self._llm.complete_structured(
            prompt,
            model_cls.model_json_schema(),
            system_prompt=SYSTEM_PROMPTS[prompt_key],
            model_name=model_name,
        )
        try:
            return model_cls.model_validate(raw)
        except ValidationError as exc:
            raise GroundworkersError(
                "QUERY_ERROR",
                f"LLM response did not match expected structure: {exc}",
            ) from exc
