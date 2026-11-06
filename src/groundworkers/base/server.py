from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import wraps
from typing import Annotated, Any, Literal, get_origin, get_type_hints

from mcp.types import ToolAnnotations
from pydantic import BaseModel

from groundworkers.base.errors import (
    ERROR_CODES,
    GroundworkersError,
    internal_error_response,
    scrub_error_message,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PromptArgument:
    """One explicitly advertised string argument for an MCP prompt."""

    name: str
    description: str | None = None
    required: bool = False


@dataclass(frozen=True)
class _RegisteredPrompt:
    """Transport-neutral prompt registration retained until ``run()``."""

    func: Callable[..., Any]
    title: str | None
    description: str | None
    arguments: tuple[PromptArgument, ...] | None


def _callable_name(func: Callable[..., Any]) -> str:
    """Registration name for a decorated callable.

    Decorators are applied to plain functions, which always carry ``__name__``.
    The fallback keeps partials and other ``__name__``-less callables registrable
    rather than raising at import time.
    """
    return getattr(func, "__name__", None) or repr(func)


class GroundworkersMCPServer:
    def __init__(self, name: str) -> None:
        self.name = name
        self._tools: dict[str, Callable[..., Any]] = {}
        self._tool_annotations: dict[str, ToolAnnotations | None] = {}
        self._prompts: dict[str, _RegisteredPrompt] = {}
        self._resources: dict[str, tuple[Callable[..., Any], str | None]] = {}

    def tool(
        self,
        name: str | None = None,
        *,
        annotations: ToolAnnotations | None = None,
        result_model: type[BaseModel] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or _callable_name(func)

            if inspect.iscoroutinefunction(func):

                @wraps(func)
                async def guarded(*args: Any, **kwargs: Any) -> Any:
                    try:
                        result = await func(*args, **kwargs)
                    except GroundworkersError as exc:
                        result = exc.to_dict()
                    except ValueError as exc:
                        result = GroundworkersError("INVALID_INPUT", str(exc)).to_dict()
                    except Exception as exc:
                        result = internal_error_response(
                            exc,
                            logger=logger,
                            boundary=f"mcp.tool.{tool_name}",
                        )
                    return _normalise_tool_result(
                        result, tool_name=tool_name, result_model=result_model
                    )
            else:

                @wraps(func)
                def guarded(*args: Any, **kwargs: Any) -> Any:
                    try:
                        result = func(*args, **kwargs)
                    except GroundworkersError as exc:
                        result = exc.to_dict()
                    except ValueError as exc:
                        result = GroundworkersError("INVALID_INPUT", str(exc)).to_dict()
                    except Exception as exc:
                        result = internal_error_response(
                            exc,
                            logger=logger,
                            boundary=f"mcp.tool.{tool_name}",
                        )
                    return _normalise_tool_result(
                        result, tool_name=tool_name, result_model=result_model
                    )

            _advertise_tool_signature(func, guarded, result_model)
            self._tools[tool_name] = guarded
            self._tool_annotations[tool_name] = annotations
            return guarded

        return decorator

    def prompt(
        self,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        arguments: list[PromptArgument] | tuple[PromptArgument, ...] | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            prompt_name = name or _callable_name(func)
            explicit_arguments = _prompt_arguments(arguments)
            registered_func = _guard_prompt_arguments(func, explicit_arguments)
            self._prompts[prompt_name] = _RegisteredPrompt(
                registered_func,
                title,
                description,
                explicit_arguments,
            )
            return registered_func

        return decorator

    def resource(
        self,
        uri: str,
        description: str | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            self._resources[uri] = (func, description)
            return func

        return decorator

    def list_tools(self) -> list[str]:
        return sorted(self._tools.keys())

    def list_prompts(self) -> list[str]:
        return sorted(self._prompts.keys())

    def list_resources(self) -> list[str]:
        return sorted(self._resources.keys())

    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        result = self._tools[name](*args, **kwargs)
        if not inspect.isawaitable(result):
            return result
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(result)
        return result

    async def call_async(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Call a registered tool from an existing async runtime."""

        result = self._tools[name](*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    def call_prompt(self, name: str, **kwargs: Any) -> Any:
        return self._prompts[name].func(**kwargs)

    def describe_tools(self) -> dict[str, dict[str, Any]]:
        description: dict[str, dict[str, Any]] = {}
        for name, func in self._tools.items():
            signature = inspect.signature(func)
            description[name] = {
                "signature": str(signature),
                "doc": inspect.getdoc(func) or "",
            }
            if (annotations := self._tool_annotations.get(name)) is not None:
                description[name]["annotations"] = annotations.model_dump()
        return description

    def describe_prompts(self) -> dict[str, dict[str, Any]]:
        description: dict[str, dict[str, Any]] = {}
        for name, prompt in self._prompts.items():
            description[name] = {
                "signature": str(inspect.signature(prompt.func)),
                "title": prompt.title or "",
                "description": prompt.description or "",
                "arguments": (
                    [
                        {
                            "name": argument.name,
                            "description": argument.description or "",
                            "required": argument.required,
                        }
                        for argument in prompt.arguments
                    ]
                    if prompt.arguments is not None
                    else None
                ),
            }
        return description

    def describe_resources(self) -> dict[str, dict[str, Any]]:
        description: dict[str, dict[str, Any]] = {}
        for uri, (func, resource_description) in self._resources.items():
            description[uri] = {
                "description": resource_description or "",
            }
        return description

    def run(
        self,
        transport: Literal["stdio", "sse", "streamable-http"] = "stdio",
        host: str = "127.0.0.1",
        port: int = 8000,
    ) -> None:
        try:
            from mcp.server.fastmcp import FastMCP
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "The official MCP Python SDK is required to run the server. Install project dependencies first."
            ) from exc

        # groundworkers is a pure request/response MCP dependency.  Use
        # stateless_http=True so that each POST is handled independently —
        # with stateful mode and json_response=True the underlying
        # StreamableHTTPServerTransport tears down its server runner task
        # when the first HTTP connection closes, removing the session from
        # _server_instances.  Subsequent calls with the same session ID then
        # hit the "Session not found" 404 path.  Stateless mode avoids this:
        # each request gets a fresh transport context while the adapters
        # (SQLAlchemy engines, connection pools, OMOP graph state) remain
        # alive for the process lifetime.
        app = FastMCP(
            self.name,
            host=host,
            port=port,
            json_response=transport == "streamable-http",
            stateless_http=True,
        )
        for tool_name, func in self._tools.items():
            app.tool(
                name=tool_name,
                description=inspect.getdoc(func) or "",
                annotations=self._tool_annotations.get(tool_name),
            )(func)
        for prompt_name, prompt in self._prompts.items():
            if prompt.arguments is None:
                app.prompt(
                    name=prompt_name,
                    title=prompt.title,
                    description=prompt.description,
                )(prompt.func)
                continue

            from mcp.server.fastmcp.prompts.base import Prompt as FastMCPPrompt
            from mcp.server.fastmcp.prompts.base import (
                PromptArgument as FastMCPPromptArgument,
            )

            app.add_prompt(
                FastMCPPrompt(
                    name=prompt_name,
                    title=prompt.title,
                    description=prompt.description,
                    arguments=[
                        FastMCPPromptArgument(
                            name=argument.name,
                            description=argument.description,
                            required=argument.required,
                        )
                        for argument in prompt.arguments
                    ],
                    fn=prompt.func,
                )
            )
        for uri, (func, description) in self._resources.items():
            app.resource(uri, description=description or "")(func)
        app.run(transport=transport)


def _prompt_arguments(
    arguments: list[PromptArgument] | tuple[PromptArgument, ...] | None,
) -> tuple[PromptArgument, ...] | None:
    """Validate explicit metadata while preserving ``None`` as infer-signature mode."""

    if arguments is None:
        return None
    resolved = tuple(arguments)
    names = [argument.name for argument in resolved]
    if any(not name for name in names):
        raise ValueError("Prompt argument names must not be empty.")
    if len(names) != len(set(names)):
        raise ValueError("Prompt argument names must be unique.")
    return resolved


def _guard_prompt_arguments(
    func: Callable[..., Any],
    arguments: tuple[PromptArgument, ...] | None,
) -> Callable[..., Any]:
    """Apply explicit prompt-argument validation without synthesising signatures."""

    if arguments is None:
        return func

    allowed = {argument.name for argument in arguments}
    required = {argument.name for argument in arguments if argument.required}

    def validate(values: dict[str, Any]) -> None:
        unknown = sorted(set(values) - allowed)
        if unknown:
            raise ValueError(f"Unknown prompt arguments: {unknown}")
        missing = sorted(required - set(values))
        if missing:
            raise ValueError(f"Missing required prompt arguments: {missing}")
        non_strings = sorted(
            name for name, value in values.items() if not isinstance(value, str)
        )
        if non_strings:
            raise ValueError(f"Prompt arguments must be strings: {non_strings}")

    if inspect.iscoroutinefunction(func):

        @wraps(func)
        async def async_guarded(**kwargs: Any) -> Any:
            validate(kwargs)
            return await func(**kwargs)

        return async_guarded

    @wraps(func)
    def guarded(**kwargs: Any) -> Any:
        validate(kwargs)
        return func(**kwargs)

    return guarded


def _sanitise_tool_result(result: Any, *, tool_name: str) -> Any:
    if not isinstance(result, dict) or result.get("error") is not True:
        return result
    code = result.get("code")
    if code not in ERROR_CODES:
        return internal_error_response(
            RuntimeError(f"Tool returned unknown error code {code!r}"),
            logger=logger,
            boundary=f"mcp.tool.{tool_name}",
        )
    safe = dict(result)
    safe["message"] = scrub_error_message(str(result.get("message", "Request failed.")))
    return safe


def _normalise_tool_result(
    result: Any,
    *,
    tool_name: str,
    result_model: type[BaseModel] | None,
) -> Any:
    if result_model is None:
        return _sanitise_tool_result(result, tool_name=tool_name)
    if isinstance(result, BaseModel):
        result = result.model_dump(mode="json")
    result = _sanitise_tool_result(result, tool_name=tool_name)
    try:
        return result_model.model_validate(result).model_dump(mode="json")
    except Exception as exc:
        failure = internal_error_response(
            exc, logger=logger, boundary=f"mcp.output.{tool_name}"
        )
        return result_model.model_validate(failure).model_dump(mode="json")


def _advertise_tool_signature(
    func: Callable[..., Any], guarded: Callable[..., Any], result_model: type[BaseModel] | None,
) -> None:
    # wraps preserves annotation strings, but FastMCP evaluates them in
    # this wrapper's globals. Resolve Annotated aliases in the original
    # tool namespace before advertising constrained schemas.
    try:
        resolved = get_type_hints(func, include_extras=True)
    except (NameError, TypeError):
        if result_model is not None:
            raise
        resolved = {}
    if result_model is not None or any(
        get_origin(value) is Annotated for value in resolved.values()
    ):
        signature = inspect.signature(func)
        advertised_signature = signature.replace(
            parameters=[
                param.replace(annotation=resolved.get(name, param.annotation))
                for name, param in signature.parameters.items()
            ],
            return_annotation=result_model
            or resolved.get("return", signature.return_annotation),
        )
        setattr(guarded, "__signature__", advertised_signature)
        guarded.__annotations__ = resolved
        if result_model is not None:
            guarded.__annotations__["return"] = result_model
