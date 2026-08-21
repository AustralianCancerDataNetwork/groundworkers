from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Callable
from functools import wraps
from typing import Any, Literal

from groundworkers.base.errors import (
    ERROR_CODES,
    GroundworkersError,
    internal_error_response,
    scrub_error_message,
)

logger = logging.getLogger(__name__)


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
        self._prompts: dict[str, tuple[Callable[..., Any], str | None]] = {}
        self._resources: dict[str, tuple[Callable[..., Any], str | None]] = {}

    def tool(self, name: str | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or _callable_name(func)

            if inspect.iscoroutinefunction(func):
                @wraps(func)
                async def guarded(*args: Any, **kwargs: Any) -> Any:
                    try:
                        result = await func(*args, **kwargs)
                    except GroundworkersError as exc:
                        return exc.to_dict()
                    except ValueError as exc:
                        return GroundworkersError("INVALID_INPUT", str(exc)).to_dict()
                    except Exception as exc:
                        return internal_error_response(
                            exc,
                            logger=logger,
                            boundary=f"mcp.tool.{tool_name}",
                        )
                    return _sanitise_tool_result(result, tool_name=tool_name)
            else:
                @wraps(func)
                def guarded(*args: Any, **kwargs: Any) -> Any:
                    try:
                        result = func(*args, **kwargs)
                    except GroundworkersError as exc:
                        return exc.to_dict()
                    except ValueError as exc:
                        return GroundworkersError("INVALID_INPUT", str(exc)).to_dict()
                    except Exception as exc:
                        return internal_error_response(
                            exc,
                            logger=logger,
                            boundary=f"mcp.tool.{tool_name}",
                        )
                    return _sanitise_tool_result(result, tool_name=tool_name)

            self._tools[tool_name] = guarded
            return guarded

        return decorator

    def prompt(
        self,
        name: str | None = None,
        description: str | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            prompt_name = name or _callable_name(func)
            self._prompts[prompt_name] = (func, description)
            return func

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
        func, _ = self._prompts[name]
        return func(**kwargs)

    def describe_tools(self) -> dict[str, dict[str, Any]]:
        description: dict[str, dict[str, Any]] = {}
        for name, func in self._tools.items():
            signature = inspect.signature(func)
            description[name] = {
                "signature": str(signature),
                "doc": inspect.getdoc(func) or "",
            }
        return description

    def describe_prompts(self) -> dict[str, dict[str, Any]]:
        description: dict[str, dict[str, Any]] = {}
        for name, (func, prompt_description) in self._prompts.items():
            signature = inspect.signature(func)
            description[name] = {
                "signature": str(signature),
                "description": prompt_description or "",
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
            app.tool(name=tool_name, description=inspect.getdoc(func) or "")(func)
        for prompt_name, (func, description) in self._prompts.items():
            app.prompt(name=prompt_name, description=description)(func)
        for uri, (func, description) in self._resources.items():
            app.resource(uri, description=description or "")(func)
        app.run(transport=transport)


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
