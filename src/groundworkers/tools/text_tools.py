from __future__ import annotations

from typing import Any

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundcrewServer
from groundworkers.services.text import TextService
from groundworkers.services.text.prompts import SYSTEM_PROMPTS, build_user_prompt


def register_text_tools(server: GroundcrewServer, text_service: TextService) -> None:
    @server.tool("text_normalize")
    def text_normalize(
        text: str,
        domain_hint: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Normalize a clinical term, abbreviation, lay phrase, or misspelling to a standard OMOP-compatible form.

        Expands abbreviations (e.g. MI → myocardial infarction), converts informal or lay language
        to clinical terminology, and corrects common misspellings — returning a single normalized phrase
        ready to pass to concept grounding tools.

        domain_hint optionally scopes normalization to a specific OMOP domain (e.g. Condition, Drug,
        Measurement) when the term is likely to be domain-specific.

        Returns: normalized, original, confidence (high/medium/low), notes.
        """
        try:
            result = text_service.normalize(text, domain_hint=domain_hint, model_name=model_name)
            return result.model_dump()
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("text_decompose")
    def text_decompose(
        text: str,
        domain_hint: str | None = None,
        max_terms: int = 10,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Decompose a free-text clinical description into a list of normalized search terms.

        Useful when a query describes a multi-concept scenario (e.g. a cohort definition, study
        inclusion criteria, or clinical note excerpt). Each returned term is normalized to a standard
        OMOP-compatible clinical name, ready to pass individually to concept_ground or
        concept_candidate_bundle.

        domain_hint optionally scopes extraction to a specific OMOP domain.
        max_terms controls the upper bound on terms returned (clamped 1–20).

        Returns: terms (list of {term, domain_hint}), original.
        """
        try:
            result = text_service.decompose(text, domain_hint=domain_hint, max_terms=max_terms, model_name=model_name)
            return result.model_dump()
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}

    @server.tool("text_disambiguate")
    def text_disambiguate(
        text: str,
        domain_hint: str | None = None,
        max_interpretations: int = 5,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Return all plausible clinical interpretations of an ambiguous term or abbreviation.

        Where text_normalize picks the single most likely meaning, text_disambiguate surfaces
        multiple candidate interpretations ranked by clinical frequency. Use this when a term
        is known to be ambiguous (e.g. MS, PCP, SOB) and the correct reading cannot be inferred
        from context — returning the ranked list to the caller to resolve.

        domain_hint optionally scopes disambiguation to a specific OMOP domain.
        max_interpretations controls the upper bound on candidates returned (clamped 1–10).

        Returns: interpretations (list of {interpretation, domain_hint, context_clues}),
        original, is_ambiguous.
        """
        try:
            result = text_service.disambiguate(
                text, domain_hint=domain_hint, max_interpretations=max_interpretations, model_name=model_name,
            )
            return result.model_dump()
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()
        except Exception as exc:
            return {"error": True, "code": "QUERY_ERROR", "message": repr(exc)}


def register_text_prompts(server: GroundcrewServer) -> None:
    """Register MCP prompt handlers for the three text preprocessing operations.

    These prompts let MCP clients request the exact message sequences that would
    be sent to the LLM for normalize, decompose, and disambiguate — useful for
    inspection, testing, or manual invocation via prompt UIs.

    System prompts are folded into the user turn because MCP only supports
    "user" and "assistant" roles; there is no "system" role in prompt messages.
    Each message's content uses the TextContent format required by FastMCP
    ({"type": "text", "text": "..."}) rather than a bare string.
    """

    @server.prompt(
        "normalize_clinical_term",
        description=(
            "Return the message sequence for normalizing a clinical term, "
            "abbreviation, or lay phrase to its OMOP-compatible equivalent."
        ),
    )
    def normalize_clinical_term(
        text: str,
        domain_hint: str = "",
    ) -> list[dict]:
        system = SYSTEM_PROMPTS["normalize"]
        user = build_user_prompt("normalize", text, domain_hint=domain_hint or None)
        return [{"role": "user", "content": {"type": "text", "text": f"{system}\n\n{user}"}}]

    @server.prompt(
        "decompose_clinical_text",
        description=(
            "Return the message sequence for decomposing a free-text clinical "
            "description into a list of normalized search terms."
        ),
    )
    def decompose_clinical_text(
        text: str,
        domain_hint: str = "",
        max_terms: int = 10,
    ) -> list[dict]:
        system = SYSTEM_PROMPTS["decompose"]
        user = build_user_prompt("decompose", text, domain_hint=domain_hint or None, max_terms=max_terms)
        return [{"role": "user", "content": {"type": "text", "text": f"{system}\n\n{user}"}}]

    @server.prompt(
        "disambiguate_clinical_term",
        description=(
            "Return the message sequence for listing all plausible clinical "
            "interpretations of an ambiguous term or abbreviation."
        ),
    )
    def disambiguate_clinical_term(
        text: str,
        domain_hint: str = "",
        max_interpretations: int = 5,
    ) -> list[dict]:
        system = SYSTEM_PROMPTS["disambiguate"]
        user = build_user_prompt(
            "disambiguate", text, domain_hint=domain_hint or None, max_interpretations=max_interpretations
        )
        return [{"role": "user", "content": {"type": "text", "text": f"{system}\n\n{user}"}}]
