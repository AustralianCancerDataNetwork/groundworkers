from __future__ import annotations

import inspect
from typing import Any, Callable


class GroundcrewServer:
    def __init__(self, name: str) -> None:
        self.name = name
        self._tools: dict[str, Callable[..., Any]] = {}
        self._prompts: dict[str, tuple[Callable[..., Any], str | None]] = {}

    def tool(self, name: str | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or func.__name__
            self._tools[tool_name] = func
            return func

        return decorator

    def prompt(
        self,
        name: str | None = None,
        description: str | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            prompt_name = name or func.__name__
            self._prompts[prompt_name] = (func, description)
            return func

        return decorator

    def list_tools(self) -> list[str]:
        return sorted(self._tools.keys())

    def list_prompts(self) -> list[str]:
        return sorted(self._prompts.keys())

    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        return self._tools[name](*args, **kwargs)

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

    def run(
        self,
        transport: str = "stdio",
        host: str = "127.0.0.1",
        port: int = 8000,
    ) -> None:
        try:
            from mcp.server.fastmcp import FastMCP
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "The official MCP Python SDK is required to run the server. Install project dependencies first."
            ) from exc

        app = FastMCP(self.name, host=host, port=port)
        for tool_name, func in self._tools.items():
            app.tool(name=tool_name)(func)
        for prompt_name, (func, description) in self._prompts.items():
            app.prompt(name=prompt_name, description=description)(func)
        app.run(transport=transport)
