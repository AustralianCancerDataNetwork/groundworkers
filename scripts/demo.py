#!/usr/bin/env python3
"""
Demo script for groundworkers Phases 3 & 4.

Exercises: concept_get, concept_by_code, concept_ancestors, concept_descendants,
           concept_ground, concept_relationships, concept_path,
           concept_map_to_standard, embedding_index_status, embedding_neighbours.

Usage:
    uv run python scripts/demo.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# ── Connection settings ──────────────────────────────────────────────────────
DB_URL = "postgresql+psycopg://airflow:airflow@localhost:19530/airflow_vector"
VOCAB_SCHEMA = "public"
FULLTEXT_SCHEMA = "public"            # set to None to disable FullTextResolver
FULLTEXT_TABLE = "concept_search"     # only used when FULLTEXT_SCHEMA is set
EMB_DEFAULT_MODEL = None              # None = auto-detect from registered models

# OMOP concept_id for "Type 2 diabetes mellitus" in this instance (SNOMED 44054006)
T2DM_ID = 201826

W = 68  # output width

# ── Helpers ──────────────────────────────────────────────────────────────────

def section(tool: str, purpose: str, inputs: dict) -> None:
    print(f"\n{'─' * W}")
    print(f"  Tool    : {tool}")
    print(f"  Purpose : {purpose}")
    for k, v in inputs.items():
        print(f"  {k:<9}: {v}")
    print('─' * W)


def error_line(value: dict) -> None:
    print(f"  ERROR  {value.get('code', '?')}: {value.get('message', repr(value))}")


def is_error(value: object) -> bool:
    return isinstance(value, dict) and "error" in value


def safe(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        return {"error": True, "code": "EXCEPTION", "message": repr(exc)}


# ── Per-type formatters ───────────────────────────────────────────────────────

def fmt_concept(c: dict) -> str:
    std = "standard" if c.get("standard_concept") else "non-standard"
    return (
        f"  concept_id   : {c['concept_id']}\n"
        f"  concept_name : {c['concept_name']}\n"
        f"  vocabulary   : {c['vocabulary_id']}  (code: {c['concept_code']})\n"
        f"  domain       : {c['domain_id']}  |  class: {c.get('concept_class_id', '—')}\n"
        f"  status       : {std}"
        + (f"  |  invalid: {c['invalid_reason']}" if c.get('invalid_reason') else "")
        + f"\n  valid dates  : {c.get('valid_start_date', '?')} → {c.get('valid_end_date', '?')}"
    )


def print_concept(c: dict) -> None:
    print(fmt_concept(c))


def print_hierarchy_list(items: list, label: str) -> None:
    if not items:
        print(f"  (no {label} found)")
        return
    print(f"  {len(items)} {label}:")
    for item in items:
        indent = "  " + "  " * (item["depth"] - 1)
        std = "S" if item.get("standard_concept") else " "
        print(
            f"{indent}  depth={item['depth']}  [{std}]  "
            f"{item['concept_id']:>10}  {item['concept_name']}"
            f"  ({item['vocabulary_id']} / {item['domain_id']})"
        )
    if len(items) > 10:
        print(f"  … (showing all {len(items)})")


def print_ground_results(results: list) -> None:
    if not results:
        print("  (no results)")
        return
    hdr = f"  {'Rank':<5}  {'Score':<8}  {'Match':<18}  {'concept_id':<12}  concept_name"
    print(hdr)
    print("  " + "─" * (W - 2))
    for i, r in enumerate(results, 1):
        name = r['concept_name'][:38]
        print(
            f"  {i:<5}  {r['total_score']:<8.4f}  {r['match_kind']:<18}  "
            f"{r['concept_id']:<12}  {name}"
        )
        print(
            f"         vocab={r.get('vocabulary_id', '—')}  "
            f"domain={r.get('domain_id', '—')}  "
            f"class={r.get('concept_class_id', '—')}"
        )


def print_edges(edges: dict, truncate: int = 4) -> None:
    out = edges.get("outbound", [])
    inn = edges.get("inbound", [])
    print(f"  Outbound edges  (concept is subject → relationship → target):  {len(out)} total")
    for e in out[:truncate]:
        valid = "" if e.get("valid") else "  [INVALID]"
        print(
            f"    [{e['predicate_kind']}]  {e['relationship_id']}"
            f"  →  {e['target_concept_id']}  \"{e.get('target_concept_name', '?')}\"{valid}"
        )
    if len(out) > truncate:
        print(f"    … (+{len(out) - truncate} more)")
    print()
    print(f"  Inbound edges   (source → relationship → concept is object):  {len(inn)} total")
    for e in inn[:truncate]:
        valid = "" if e.get("valid") else "  [INVALID]"
        print(
            f"    {e['source_concept_id']}  \"{e.get('source_concept_name', '?')}\""
            f"  →  [{e['predicate_kind']}]  {e['relationship_id']}{valid}"
        )
    if len(inn) > truncate:
        print(f"    … (+{len(inn) - truncate} more)")


def print_path(result: dict, source_id: int, target_id: int) -> None:
    if not result.get("found"):
        print(f"  No path found between {source_id} and {target_id}")
        return
    paths = result.get("paths", [])
    print(f"  {len(paths)} path(s) found:")
    for pi, path in enumerate(paths, 1):
        steps = path.get("steps", [])
        print(f"\n  Path {pi}  ({path['length']} step(s)):")
        if not steps:
            print(f"    {source_id}  (source == target, trivial)")
            return
        for step in steps:
            print(f"    {step['subject_id']}  \"{step.get('subject_name', '?')}\"")
            print(
                f"      ──[{step['predicate_kind']} / {step['predicate']}]──▶"
            )
        last = steps[-1]
        print(f"    {last['object_id']}  \"{last.get('object_name', '?')}\"")


def print_map_to_standard(result: dict) -> None:
    src = result["source"]
    std_list = result["standard_concepts"]
    src_std = "standard" if src.get("standard_concept") else "non-standard"
    print(
        f"  Source  ({src_std}):  "
        f"{src['vocabulary_id']} {src['concept_code']}  "
        f"\"{src['concept_name']}\"  (concept_id={src['concept_id']})"
    )
    if not std_list:
        print("  No standard mapping found.")
        return
    print(f"  Maps to {len(std_list)} standard concept(s):")
    for c in std_list:
        print(
            f"    →  {c['concept_id']}  \"{c['concept_name']}\""
            f"  [{c['vocabulary_id']} {c['concept_code']}]"
            f"  domain={c['domain_id']}"
        )


def print_emb_status(status: dict) -> None:
    avail = status.get("available")
    print(f"  available    : {avail}")
    if avail:
        print(f"  backend      : {status.get('backend_type', '—')}")
        for m in status.get("models", []):
            name = m.get("model_name", "?")
            count = m.get("concept_count", "?")
            dims = m.get("dimensions", "?")
            print(f"  model        : {name}  ({count} concepts, {dims}d)")


def print_neighbours(results: list) -> None:
    if not results:
        print("  (no results)")
        return
    hdr = f"  {'Rank':<5}  {'Score':<8}  {'concept_id':<12}  concept_name"
    print(hdr)
    print("  " + "─" * (W - 2))
    for i, r in enumerate(results, 1):
        print(
            f"  {i:<5}  {r.get('similarity', 0.0):<8.4f}  "
            f"{r['concept_id']:<12}  {r.get('concept_name', '?')}"
        )
        print(
            f"         vocab={r.get('vocabulary_id', '—')}  "
            f"domain={r.get('domain_id', '—')}"
        )


# ── Build adapters ────────────────────────────────────────────────────────────

def build_graph_adapter():
    from sqlalchemy import create_engine

    from groundworkers.adapters.omop_graph import OmopGraphAdapter

    engine = create_engine(DB_URL)
    return OmopGraphAdapter(
        engine=engine,
        vocab_schema=VOCAB_SCHEMA,
        fulltext_schema=FULLTEXT_SCHEMA,
        fulltext_table=FULLTEXT_TABLE,
    )


def build_emb_adapter(graph_engine):
    from groundworkers.adapters.omop_emb import OmopEmbAdapter

    def backend_factory():
        from omop_emb.backends.pgvector import PGVectorEmbeddingBackend
        from sqlalchemy import create_engine as _ce
        return PGVectorEmbeddingBackend(emb_engine=_ce(DB_URL))

    return OmopEmbAdapter(
        backend_factory=backend_factory,
        backend_type="pgvector",
        default_model_name=EMB_DEFAULT_MODEL,
        cdm_engine=graph_engine,
    )


# ── Demo sections ─────────────────────────────────────────────────────────────

def demo_concept_lookup(graph):
    section(
        "concept_get",
        "Retrieve a single OMOP concept by its concept_id",
        {"Input": f"concept_id={T2DM_ID}"},
    )
    result = safe(graph.get_concept, T2DM_ID)
    if is_error(result):
        error_line(result)
    else:
        print_concept(result)

    section(
        "concept_by_code",
        "Look up a concept by its source vocabulary code",
        {"Input": "vocabulary=SNOMED, code=44054006  (Type 2 diabetes mellitus)"},
    )
    result = safe(graph.get_concept_by_code, "SNOMED", "44054006")
    if is_error(result):
        error_line(result)
    elif not isinstance(result, list) or not result:
        print("  (not found)")
    else:
        print_concept(result[0])
        if len(result) > 1:
            print(f"  … (+{len(result) - 1} more matches)")


def demo_hierarchy(graph):
    section(
        "concept_ancestors",
        "Walk the IS-A hierarchy upward (breadth-first, returns all levels up to max_depth)",
        {"Input": f"concept_id={T2DM_ID}, max_depth=3"},
    )
    result = safe(graph.get_ancestors, T2DM_ID, 3)
    if is_error(result):
        error_line(result)
    elif isinstance(result, list):
        print_hierarchy_list(result, "ancestors")

    section(
        "concept_descendants",
        "Walk the IS-A hierarchy downward (breadth-first, returns all levels up to max_depth)",
        {"Input": f"concept_id={T2DM_ID}, max_depth=1"},
    )
    result = safe(graph.get_descendants, T2DM_ID, 1)
    if is_error(result):
        error_line(result)
    elif isinstance(result, list):
        print_hierarchy_list(result, "descendants")


def demo_grounding(graph):
    section(
        "concept_ground",
        "Map free text to ranked standard OMOP concepts via exact, partial, fulltext and\n"
        "  embedding resolvers with ancestry-validated deduplication",
        {
            "Input": 'query="Type 2 diabetes mellitus", domain=Condition, limit=5',
            "Expects": "EXACT match → concept 201826 at rank 1",
        },
    )
    result = safe(graph.ground, "Type 2 diabetes mellitus", 5, "Condition", None)
    if is_error(result):
        error_line(result)
    elif isinstance(result, list):
        print_ground_results(result)

    section(
        "concept_ground",
        "Same tool — partial/ambiguous query with domain filter",
        {"Input": 'query="lung cancer", domain=Condition, limit=5'},
    )
    result = safe(graph.ground, "lung cancer", 5, "Condition", None)
    if is_error(result):
        error_line(result)
    elif isinstance(result, list):
        print_ground_results(result)


def demo_relationships(graph):
    section(
        "concept_relationships",
        "Return all outbound and inbound edges for a concept\n"
        "  (predicate kinds: HIERARCHY=is-a, IDENTITY=maps-to, COMPOSITION, ASSOCIATION)",
        {"Input": f"concept_id={T2DM_ID}"},
    )
    edges = safe(graph.get_edges, T2DM_ID)
    if is_error(edges):
        error_line(edges)
    else:
        print_edges(edges)


def demo_path(graph):
    ancestors = safe(graph.get_ancestors, T2DM_ID, 1)
    if not isinstance(ancestors, list) or not ancestors:
        section("concept_path", "Find shortest path(s) between two concepts", {"Input": "—"})
        print("  (no ancestors found — skipping path demo)")
        return

    parent_id = ancestors[0]["concept_id"]
    parent_name = ancestors[0].get("concept_name", str(parent_id))

    section(
        "concept_path",
        "Find shortest path(s) between two concepts in the OMOP graph",
        {
            "Input": f"source={T2DM_ID} (Type 2 diabetes mellitus)  →  "
                     f"target={parent_id} ({parent_name})",
            "Note": "direct parent — expect 1-step HIERARCHY path",
        },
    )
    result = safe(graph.find_path, T2DM_ID, parent_id, 5)
    if is_error(result):
        error_line(result)
    else:
        print_path(result, T2DM_ID, parent_id)

    section(
        "concept_path",
        "Edge case: source == target (should return found=True, 0 steps)",
        {"Input": f"source={T2DM_ID}  ==  target={T2DM_ID}"},
    )
    result = safe(graph.find_path, T2DM_ID, T2DM_ID, 5)
    if is_error(result):
        error_line(result)
    else:
        print_path(result, T2DM_ID, T2DM_ID)


def demo_map_to_standard(graph):
    section(
        "concept_map_to_standard",
        "Map a non-standard source code to its standard OMOP concept(s)\n"
        "  by following IDENTITY edges in the concept graph",
        {
            "Input": "vocabulary=ICD10CM, code=E11.9  (Type 2 diabetes mellitus w/o complications)",
            "Expects": "non-standard ICD10CM concept → standard SNOMED concept 201826",
        },
    )
    result = safe(graph.map_to_standard, "ICD10CM", "E11.9")
    if is_error(result):
        error_line(result)
    else:
        print_map_to_standard(result)


def demo_embeddings(emb):
    section(
        "embedding_index_status",
        "Check which embedding models are loaded and available for similarity search",
        {"Input": "(none)"},
    )
    status = safe(emb.index_status)
    if is_error(status):
        error_line(status)
    else:
        print_emb_status(status)

    if not (isinstance(status, dict) and status.get("available")):
        print("\n  (embedding backend unavailable — skipping neighbour demo)")
        return

    section(
        "embedding_neighbours",
        "Return the N most semantically similar concepts via vector similarity search",
        {
            "Input": f"concept_id={T2DM_ID} (Type 2 diabetes mellitus), limit=5",
            "Note": "uses pgvector cosine similarity on pre-computed concept embeddings",
        },
    )
    result = safe(emb.get_neighbours, T2DM_ID, 5, EMB_DEFAULT_MODEL)
    if is_error(result):
        error_line(result)
    elif isinstance(result, dict) and "results" in result:
        print_neighbours(result["results"])
    else:
        print_neighbours(result if isinstance(result, list) else [])


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    print("=" * W)
    print("  groundworkers  —  OMOP concept tool demo")
    print("=" * W)
    print(f"  database     : {DB_URL}")
    print(f"  vocab_schema : {VOCAB_SCHEMA}")
    print(f"  fulltext     : {FULLTEXT_SCHEMA}.{FULLTEXT_TABLE}" if FULLTEXT_SCHEMA else "  fulltext     : disabled")
    print(f"  pivot concept: {T2DM_ID}  (Type 2 diabetes mellitus, SNOMED 44054006)")
    print()

    print("  Initialising graph adapter …", end=" ", flush=True)
    graph = build_graph_adapter()
    print("ok")

    print("  Initialising embedding adapter …", end=" ", flush=True)
    emb = build_emb_adapter(graph.engine)
    print("ok")

    demo_concept_lookup(graph)
    demo_hierarchy(graph)
    demo_grounding(graph)
    demo_relationships(graph)
    demo_path(graph)
    demo_map_to_standard(graph)
    demo_embeddings(emb)

    print(f"\n{'═' * W}")
    print("  Demo complete.")
    print('═' * W)

    graph.close()
    emb.close()


if __name__ == "__main__":
    main()
