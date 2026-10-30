"""Agent-surface snapshot (T0): exactly what an MCP client is shown.

The surface is collected through the real MCP protocol: the FastMCP app that
``GroundworkersMCPServer.run`` would serve is captured and queried with an
in-memory client, so names, descriptions, input/output schemas and
annotations are the ones an agent receives.

Two surfaces:

* ``reference`` -- a fixed stack (CDM + embedding store + chat model, no
  plugins) built without live services. Deterministic, so CI can compare it
  with the committed ``evaluation/surface/reference.json``.
* ``live`` -- whatever the local stack config and backend health produce.
  Evidence only; never gated.

Usage::

    python -m tests.support.agent_surface --check     # compare with reference
    python -m tests.support.agent_surface --write     # accept the current surface
    python -m tests.support.agent_surface --live --output <file>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

SCHEMA = "groundworkers.agent-surface/v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
SURFACE_DIR = REPO_ROOT / "evaluation" / "surface"
REFERENCE_PATH = SURFACE_DIR / "reference.json"
BUDGET_PATH = SURFACE_DIR / "budget.json"
REGENERATE_COMMAND = "python -m tests.support.agent_surface --write"

_BOUND_KEYS = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum")


def _compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def canonical_json(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True) + "\n"


def _numeric_branches(schema: dict[str, Any]) -> list[dict[str, Any]]:
    branches = schema.get("anyOf") or schema.get("oneOf") or [schema]
    return [b for b in branches if b.get("type") in ("integer", "number")]


def unbounded_numeric_params(input_schema: dict[str, Any]) -> list[str]:
    """Numeric parameters whose schema carries no lower or upper bound.

    A small model only sees the schema; a bound enforced at runtime (or a
    silent clamp) is invisible to it.
    """
    names = []
    for name, prop in sorted(input_schema.get("properties", {}).items()):
        for branch in _numeric_branches(prop):
            if not any(key in branch for key in _BOUND_KEYS):
                names.append(name)
                break
    return names


def tool_metrics(tool: dict[str, Any]) -> dict[str, Any]:
    input_schema = tool.get("inputSchema") or {}
    output_schema = tool.get("outputSchema")
    params = sorted(input_schema.get("properties", {}))
    description = tool.get("description") or ""
    return {
        "chars": len(_compact(tool)),
        # What clients generally put in the model's context: name, description
        # and input schema. Output schemas and annotations often are not sent.
        "model_visible_chars": len(tool["name"]) + len(description) + len(_compact(input_schema)),
        "description_chars": len(description),
        "input_schema_chars": len(_compact(input_schema)),
        "output_schema_chars": len(_compact(output_schema)) if output_schema else 0,
        "params": len(params),
        "has_annotations": bool(tool.get("annotations")),
        # FastMCP derives an output schema even from ``dict[str, Any]``; only
        # one that names its fields tells a client anything.
        "has_typed_output_schema": bool(output_schema and output_schema.get("properties")),
        "exposes_model_name": "model_name" in params,
        "unbounded_numeric_params": unbounded_numeric_params(input_schema),
    }


def surface_document(
    tools: list[dict[str, Any]],
    prompts: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    resource_templates: list[dict[str, Any]],
) -> dict[str, Any]:
    tools = sorted(tools, key=lambda t: t["name"])
    per_tool = {t["name"]: tool_metrics(t) for t in tools}
    total_chars = sum(m["chars"] for m in per_tool.values())
    visible_chars = sum(m["model_visible_chars"] for m in per_tool.values())
    return {
        "schema": SCHEMA,
        "summary": {
            "tools": len(tools),
            "prompts": len(prompts),
            "resources": len(resources) + len(resource_templates),
            "model_visible_tool_chars": visible_chars,
            # Fixed chars/4 rule: comparable across runs, not a tokenizer count.
            "approx_model_visible_tokens": visible_chars // 4,
            "total_tool_chars": total_chars,
            "tools_with_annotations": sum(m["has_annotations"] for m in per_tool.values()),
            "tools_with_typed_output_schema": sum(
                m["has_typed_output_schema"] for m in per_tool.values()
            ),
            "tools_exposing_model_name": sum(m["exposes_model_name"] for m in per_tool.values()),
            "unbounded_numeric_params": sum(
                len(m["unbounded_numeric_params"]) for m in per_tool.values()
            ),
        },
        "tool_metrics": per_tool,
        "tools": tools,
        "prompts": sorted(prompts, key=lambda p: p["name"]),
        "resources": sorted(resources, key=lambda r: r["uri"]),
        "resource_templates": sorted(resource_templates, key=lambda r: r["uriTemplate"]),
    }


def _dump(items: list[Any]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in items]


async def _collect(app: Any) -> dict[str, Any]:
    from mcp.shared.memory import create_connected_server_and_client_session

    async with create_connected_server_and_client_session(app) as client:
        tools = (await client.list_tools()).tools
        prompts = (await client.list_prompts()).prompts
        listed = (await client.list_resources()).resources
        templates = (await client.list_resource_templates()).resourceTemplates
    return surface_document(_dump(tools), _dump(prompts), _dump(listed), _dump(templates))


def collect_surface(server: Any) -> dict[str, Any]:
    """Serve *server* through FastMCP in memory and record what a client sees."""
    from mcp.server.fastmcp import FastMCP

    captured: dict[str, Any] = {}
    with patch.object(FastMCP, "run", lambda app, **kwargs: captured.update(app=app)):
        server.run()
    return asyncio.run(_collect(captured["app"]))


@contextmanager
def reference_server() -> Iterator[Any]:
    """The fixed reference stack: CDM + embedding store + chat model, no plugins.

    Backends are lazy, so nothing is contacted. Registration currently follows
    configuration rather than health, so every configured tool is listed.
    """
    from groundworkers.bootstrap import build_app_config_from_stack
    from groundworkers.server import create_server
    from tests.support.stack_config import add_chat_model, build_embedding_stack

    with tempfile.TemporaryDirectory() as tmp:
        stack = build_embedding_stack()
        stack.connections["embedding_main"].database_name = str(Path(tmp) / "emb.db")
        add_chat_model(stack)
        config = build_app_config_from_stack(stack)
        with patch("groundworkers.app.discover_plugins", return_value=[]):
            yield create_server(config)


def reference_surface() -> dict[str, Any]:
    with reference_server() as server:
        return collect_surface(server)


def live_surface(config_path: str | None = None) -> dict[str, Any]:
    from groundworkers.bootstrap import build_app_config
    from groundworkers.server import create_server

    return collect_surface(create_server(build_app_config(config_path=config_path)))


def diff_surfaces(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Human-readable differences, most useful first. Empty means identical."""
    lines: list[str] = []
    old_tools = {t["name"]: t for t in old.get("tools", [])}
    new_tools = {t["name"]: t for t in new.get("tools", [])}
    for name in sorted(new_tools.keys() - old_tools.keys()):
        lines.append(f"+ tool {name}")
    for name in sorted(old_tools.keys() - new_tools.keys()):
        lines.append(f"- tool {name}")
    for name in sorted(old_tools.keys() & new_tools.keys()):
        before, after = old_tools[name], new_tools[name]
        changed = sorted(
            key for key in before.keys() | after.keys() if before.get(key) != after.get(key)
        )
        if changed:
            delta = len(_compact(after)) - len(_compact(before))
            lines.append(f"~ tool {name}: {', '.join(changed)} ({delta:+d} chars)")
    for kind, key in (("prompt", "name"), ("resource", "uri"), ("resource_template", "uriTemplate")):
        plural = "resource_templates" if kind == "resource_template" else f"{kind}s"
        before = {item[key]: item for item in old.get(plural, [])}
        after = {item[key]: item for item in new.get(plural, [])}
        lines += [f"+ {kind} {k}" for k in sorted(after.keys() - before.keys())]
        lines += [f"- {kind} {k}" for k in sorted(before.keys() - after.keys())]
        lines += [
            f"~ {kind} {k}" for k in sorted(before.keys() & after.keys()) if before[k] != after[k]
        ]
    old_summary, new_summary = old.get("summary", {}), new.get("summary", {})
    for key in sorted(old_summary.keys() | new_summary.keys()):
        if old_summary.get(key) != new_summary.get(key):
            lines.append(f"  summary.{key}: {old_summary.get(key)} -> {new_summary.get(key)}")
    return lines


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def budget_violation(document: dict[str, Any], budget: dict[str, Any]) -> str | None:
    total = document["summary"]["model_visible_tool_chars"]
    limit = budget["max_model_visible_tool_chars"]
    if total <= limit:
        return None
    return (
        f"Model-visible tool surface is {total} chars, over the {limit}-char budget in "
        f"{BUDGET_PATH.relative_to(REPO_ROOT)}. Shrink the surface, or raise the "
        "budget there with a dated reason."
    )


def _print_summary(document: dict[str, Any]) -> None:
    for key, value in document["summary"].items():
        print(f"{key}: {value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="Compare the reference surface with the committed snapshot.")
    mode.add_argument("--write", action="store_true", help="Write the reference surface to the committed snapshot.")
    mode.add_argument("--live", action="store_true", help="Snapshot the surface of the local stack config.")
    parser.add_argument("--output", type=Path, help="Where --live writes its snapshot (default: stdout summary only).")
    parser.add_argument("--config-path", help="Stack config for --live (default: the usual locator).")
    args = parser.parse_args(argv)

    if args.live:
        document = live_surface(args.config_path)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(canonical_json(document), encoding="utf-8")
            print(f"wrote {args.output}")
        _print_summary(document)
        return 0

    document = reference_surface()
    if args.write:
        SURFACE_DIR.mkdir(parents=True, exist_ok=True)
        previous = load_json(REFERENCE_PATH) if REFERENCE_PATH.exists() else {}
        REFERENCE_PATH.write_text(canonical_json(document), encoding="utf-8")
        print(f"wrote {REFERENCE_PATH.relative_to(REPO_ROOT)}")
        for line in diff_surfaces(previous, document):
            print(line)
        return 0

    differences = diff_surfaces(load_json(REFERENCE_PATH), document)
    for line in differences:
        print(line)
    violation = budget_violation(document, load_json(BUDGET_PATH))
    if violation:
        print(violation)
    if differences:
        print(f"Agent surface differs from the snapshot. If intended: {REGENERATE_COMMAND}")
    return 1 if differences or violation else 0


if __name__ == "__main__":
    sys.exit(main())
