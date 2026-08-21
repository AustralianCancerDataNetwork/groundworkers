# Source planning tools

Source planning is stateless pre-ingest analysis. It turns source content into neutral, typed artifacts that an orchestration layer can inspect, route, or pass to a later ingestion step. It does not persist a job or decide the final mapping.

The tools accept UTF-8 text by default and can also decode base64 content. Format-specific readers are optional; install the extra for the file formats you submit.

## `source_plan`

Runs deterministic planning from source content.

```json
{
  "content": "field_name,field_label\nhba1c,Haemoglobin A1c\n",
  "filename": "dictionary.csv",
  "caller_hint": "data_dictionary"
}
```

The result may include detected source format, normalized and annotated tables, column roles, domain hints, an ingestion strategy, warnings, and provenance. Set `include_intermediate` when the caller needs the raw, normalized, and annotated stages for inspection.

## `source_plan_assisted`

Runs the same pipeline with an explicit LLM-assisted classification step. Use it after deterministic planning when the available structural and header evidence is not sufficient. The result records whether the LLM tier was used; it does not hide the fallback provenance.

```json
{
  "content": "field_name,field_label\nhba1c,Haemoglobin A1c\n",
  "filename": "dictionary.csv",
  "caller_hint": "data_dictionary",
  "include_intermediate": false
}
```

This path returns `BACKEND_UNAVAIL` if no configured chat model can perform the assisted step. The deterministic `source_plan` path does not require an LLM.

## Supporting MCP resources

The server also exposes resources describing the current source-planning vocabulary:

- `source-planning://canonical-headers`
- `source-planning://column-roles`
- `source-planning://ingestion-strategies`

These resources help an agent interpret planning output without hard-coding the enum values.

## Relationship to groundcrew

`groundcrew` owns stateful ingestion orchestration. It can call these worker-side capabilities over MCP, then apply its own job policy and human-review flow.
