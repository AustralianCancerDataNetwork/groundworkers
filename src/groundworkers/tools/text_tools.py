from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundworkersMCPServer
from groundworkers.services.text import TextService
from groundworkers.services.text.prompts import SYSTEM_PROMPTS, build_user_prompt
from groundworkers.toolsets import Toolset


def register_text_tools(
    server: GroundworkersMCPServer, text_service: TextService
) -> None:
    @server.tool("text_normalize", toolsets=(Toolset.LLM,))
    async def text_normalize(
        text: str,
        domain_hint: Annotated[
            str | None,
            Field(description="Optional context, used only to resolve ambiguity."),
        ] = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Language-model expansion of a short term: clear abbreviations, lay wording and obvious
        misspellings, keeping meaning and specificity. Use it when word-based search fails on
        shorthand; embedding search already tolerates much of this. Returns the normalised text
        with a confidence."""
        try:
            result = await text_service.async_normalize(
                text,
                domain_hint=domain_hint,
                model_name=model_name,
            )
            return result.model_dump()
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("text_mapping_cleanup", toolsets=(Toolset.LLM,))
    async def text_mapping_cleanup(
        text: str,
        context: Annotated[
            dict[str, Any] | None,
            Field(
                description="Optional object with surrounding information, for example the parent label, sibling values or source code."
            ),
        ] = None,
        domain_hint: str | None = None,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Language-model rewrite of one data-dictionary label or value into the best single search
        phrase, removing form noise (scoring syntax, code prefixes, duplicated fragments) while
        keeping meaning and specificity. Returns the phrase, a confidence and the model's own
        changed flag."""
        try:
            result = await text_service.async_mapping_cleanup(
                text,
                context=context,
                domain_hint=domain_hint,
                model_name=model_name,
            )
            return result.model_dump()
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("text_decompose", toolsets=(Toolset.LLM,))
    async def text_decompose(
        text: str,
        domain_hint: Annotated[
            str | None,
            Field(
                description="Optional context such as a domain name; it guides the model and is not a filter."
            ),
        ] = None,
        max_terms: Annotated[
            int, Field(description="Requested maximum number of terms, 1-20.")
        ] = 10,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Language-model split of free text naming several concepts (criteria, note excerpts) into
        the distinct clinical terms it states, each normalised, to ground one by one with
        concept_candidate_bundle. The terms are model text, not OMOP concepts; max_terms is a
        request to the model, not an enforced limit."""
        try:
            result = await text_service.async_decompose(
                text,
                domain_hint=domain_hint,
                max_terms=max_terms,
                model_name=model_name,
            )
            return result.model_dump()
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()

    @server.tool("text_disambiguate", toolsets=(Toolset.LLM,))
    async def text_disambiguate(
        text: str,
        domain_hint: Annotated[
            str | None,
            Field(
                description="Optional context such as a domain name; it guides the model and is not a filter."
            ),
        ] = None,
        max_interpretations: Annotated[
            int, Field(description="Requested maximum number of meanings, 1-10.")
        ] = 5,
        model_name: str | None = None,
    ) -> dict[str, Any]:
        """Language-model list of the plausible meanings of an ambiguous term or abbreviation, most
        likely first given the context (no frequency data is used), with is_ambiguous. Ground
        each meaning separately; max_interpretations is a request to the model, not an enforced
        limit."""
        try:
            result = await text_service.async_disambiguate(
                text,
                domain_hint=domain_hint,
                max_interpretations=max_interpretations,
                model_name=model_name,
            )
            return result.model_dump()
        except ValueError as exc:
            return {"error": True, "code": "INVALID_INPUT", "message": str(exc)}
        except GroundworkersError as exc:
            return exc.to_dict()


def register_text_prompts(server: GroundworkersMCPServer) -> None:
    """Register MCP prompt handlers for the text preprocessing operations.

    These prompts let MCP clients request the exact message sequences that would
    be sent to the LLM for normalize, mapping cleanup, decompose, and
    disambiguate — useful for inspection, testing, or manual invocation via
    prompt UIs.

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
        return [
            {"role": "user", "content": {"type": "text", "text": f"{system}\n\n{user}"}}
        ]

    @server.prompt(
        "cleanup_mapping_text",
        description=(
            "Return the message sequence for rewriting source text into a "
            "clean OMOP-grounding search phrase."
        ),
    )
    def cleanup_mapping_text(
        text: str,
        domain_hint: str = "",
        context: dict[str, Any] | None = None,
    ) -> list[dict]:
        system = SYSTEM_PROMPTS["mapping_cleanup"]
        user = build_user_prompt(
            "mapping_cleanup",
            text,
            domain_hint=domain_hint or None,
            context=context or {},
        )
        return [
            {"role": "user", "content": {"type": "text", "text": f"{system}\n\n{user}"}}
        ]

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
        user = build_user_prompt(
            "decompose", text, domain_hint=domain_hint or None, max_terms=max_terms
        )
        return [
            {"role": "user", "content": {"type": "text", "text": f"{system}\n\n{user}"}}
        ]

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
            "disambiguate",
            text,
            domain_hint=domain_hint or None,
            max_interpretations=max_interpretations,
        )
        return [
            {"role": "user", "content": {"type": "text", "text": f"{system}\n\n{user}"}}
        ]
