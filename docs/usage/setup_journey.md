# Setup journey

This is the end-to-end path from a fresh install to a working Groundworkers
server, in the order an analyst actually walks it. Every configuration write goes
through the same guided flow, so the steps look and behave alike.

Launch the setup console:

```bash
groundworkers --tui
```

The console shows five sections: **Database**, **Graph**, **LLM Provider**,
**Embeddings**, and **Chat**. Each one reports its own status, so you can see what
is still missing without reading the TOML.

## 1. CDM database (required)

Everything else is optional; this is not.

Select **Database → Configure**. The wizard asks for:

1. **Connection** — a name for the physical connection and the database type
   (SQLite or PostgreSQL).
2. **Server details** — host, port, user, password. Skipped for SQLite.
3. **Database** — the database name. For SQLite this is the file path.
4. **CDM description** — the logical entry name, the CDM schema, the vocabulary
   schema, and an optional results schema.

Before applying, the wizard shows exactly which entries and fields will change.
Passwords appear as `<redacted>` — the review never displays a secret you typed.

Applying writes three things: a `[connections.*]` entry, a `[databases.*]` entry
with `kind = "cdm"`, and the `cdm_db` reference in `[tools.groundworkers]`.

Then use **Test connections** to confirm the server is reachable and the CDM and
vocabulary schemas are present. This is a read-only check, separate from the write
flow — testing never modifies your configuration.

At this point the server runs. You have vocabulary search, the concept graph, and
lexical grounding (exact, full-text, and partial matching). Embedding status
reports as unconfigured, which is expected, not an error.

## 2. Graph

There is nothing to configure here. Graph traversal uses the CDM database you
just set up. The section reports which database and vocabulary schema it will use,
plus the grounding policy Groundworkers owns (`max_depth` and
`min_fulltext_overlap`).

If the panel says the CDM database is not verified, go back and run **Test
connections**.

## 3. Chat model (optional)

Needed only for the text and domain tools and for LLM-assisted source planning.

Select **LLM Provider → Configure**:

1. **Provider** — Ollama or an OpenAI-compatible endpoint — plus the endpoint URL
   and an optional API key.
2. **Chat model** — chosen from a list the provider actually reports.

The model list is fetched live from the endpoint when you complete the first step.
If the endpoint is unreachable, you get an actionable message and stay on the
provider step; the message never echoes your API key.

On a later edit, leaving the API key blank keeps the stored one. You do not have
to retype a credential to change the model.

## 4. Embedding model and vector store (optional)

Embedding search and the embedding grounding tier need **both** an embedding model
and a vector store. With only one configured, status says so and embedding
features stay off rather than guessing.

Select **Embeddings → Configure model**. The wizard mirrors the chat journey:
provider first, then a model chosen from live discovery. Applying writes a
`[providers.*]` entry, a `[models.*]` entry with `embeddings = true`, and the
`embedding_model_name` reference.

The vector store is a `[vector_stores.*]` entry with `backend_type` of
`sqlitevec` or `pgvector` and a `database` reference, plus the
`vector_store_name` reference.

!!! note "The embedding model is not the chat model"
    `embedding_model_name` and `llm_model_name` are separate references
    and are never substituted for one another. Configuring one does not configure
    the other.

## 5. Populate the vector store

A configured store is an empty store. Until it holds vectors, the embedding
grounding tier has nothing to match against.

Use **Embeddings → Refresh coverage** to compare CDM vocabulary counts against
what the store holds. The panel lists each vocabulary with its pending count, so
you can populate everything or select specific vocabularies.

Then use **Populate**. This is deliberately an explicit operator action:
Groundworkers never writes embeddings during a query. The server holds the graph
read-only, so a query can encode your search text but can never mutate the store.

Once the store is populated, `concept_ground` will use the embedding tier for
queries that no lexical tier can answer.

## Reading grounding results

Grounding results carry strict OMOP flags. Check them rather than assuming:

| Field | Meaning |
|---|---|
| `standard_concept` | true only for raw `standard_concept = 'S'` |
| `classification_concept` | true only for raw `standard_concept = 'C'` |
| `is_active` | not deprecated or upgraded |

Grounding can legitimately return a classification concept — an ATC or CPT4
hierarchy node. Those are real positions in the hierarchy but **not** valid
mapping targets for a CDM entity field. Grounding `metformin`, for example,
returns the RxNorm ingredient as standard and the ATC `metformin; oral` node as
classification.

`grounding_explanation` tells you which tier matched, whether embedding scoring
was used, and — if the embedding tier was planned but could not run —
`embedding_tier_detail` explains why. A lexical-only answer is never presented as
a complete one.

## If someone else edits the config

Apply is revision-checked. If the file changed after your review was prepared, the
apply returns **conflicted** and the other writer's file is left untouched. Reload,
review the new state, and try again.

Configuration writes are local console operations. There are no MCP or REST tools
that write configuration.
