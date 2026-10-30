# Ancestry MCP contract design

Status: implemented in Groundworkers. The MCP tool, typed envelope, catalogue validation and canonical ancestor displays follow this contract; the temporary ancestry query remains isolated for future migration into omop-graph. Restart an existing MCP server and refresh client discovery before inference sessions.

## Goals and ownership

Expose complete stored OMOP ancestry consistently, make abstention understandable, and let an interactive mapping agent request an appropriate target class without imposing a drug policy on all callers. Preserve the temporary primitive's migration route into omop-graph.

- `adapters/_omop_graph_ancestry.py`: portable SQL query only, as implemented.
- `OmopGraphAdapter`: query execution, normalized concept metadata and backend errors.
- `GraphService`: canonical stored-ancestry retrieval with stored distances.
- `MappingService`: common-set intersection, eligibility, constrained ranking and explicit ties.
- `MappingContext`: same service capability for plugins.
- MCP tools: validated inputs, typed response, concise help and resource limits.
- PBS plugin or interactive agent: interpretation of source ingredients, form, strength, route, release type and package; clinical acceptance and broad-match labels.

Do not expose a low-level SQL/query-building tool. Do not introduce a second generic grounding tool for this feature.

## Public MCP operation

Register `concept_shared_standard_ancestor` in `tools/mapping_tools.py` under the existing mapping-service availability gate. This name agrees with the existing Python/plugin operation.

Proposed signature (bounds must be in generated JSON Schema as well as runtime validation):

```python
concept_shared_standard_ancestor(
    concept_ids: list[positive_strict_int],  # 2..200 supplied IDs; >=2 distinct
    max_depth: int = 5,                    # 0..20, stored minimum separation
    concept_class_ids: list[str] | None = None,
    vocabulary_ids: list[str] | None = None,
    limit: int = 10,                       # 1..50 displayed common candidates
    offset: int = 0,                       # >=0, display pagination only
) -> SharedAncestorResponse
```

`positive_strict_int` denotes an Annotated/Pydantic strict integer constrained above zero, not a new wire type. Reject booleans, floats and string coercion. Deduplicate IDs in first-seen order. Enforce the supplied-list cap before deduplication; reject fewer than two distinct IDs. Do not silently truncate inputs.

Omitted/null class or vocabulary constraints mean unrestricted. Empty lists and blank values are invalid. Trim and deduplicate filter values, validate them against the installed catalogue, and return INVALID_INPUT for unknown identifiers, naming the unrecognized values. A recognized vocabulary with no qualifying targets yields a scoped no-match. Exact vocabulary IDs are required; requesting RxNorm alone does not include RxNorm Extension. Request both when appropriate.

Keep `max_depth` for naming consistency but document that it measures stored `min_levels_of_separation`, not recursive parent hops. Explicit zero is supported. Reject bounds violations rather than silently clamping them. Use one shared validator for Python, plugin and MCP paths so these paths do not disagree on filter semantics. The existing Python test that treats an empty constraint list as no-match must change to the explicit invalid-input contract.

Suggested tool description:

> Find common active standard OMOP ancestors of two or more known concept IDs. Use this to test whether alternative candidate concepts for the same source record converge on a shared representation. Optional class and vocabulary constraints apply before selection. Returns a unique nearest eligible ancestor or an explicit ambiguous/no-match result. Distances come from CONCEPT_ANCESTOR, including distinct zero-separation ancestors. A shared ancestor is a generalization; it does not establish equivalence or clinical mapping correctness. Do not use this operation to merge separate combination-drug ingredients into a product.

Preserve the same-domain requirement; it must be stated in help and echoed in the result. Mixed source domains produce an incompatible-inputs outcome with the observed domain IDs. Do not discard disagreeing inputs to obtain a match.

## Selection and completeness

1. Validate and load every input concept; unknown IDs are NOT_FOUND, backend failure is an error.
2. Retrieve all stored ancestry rows satisfying max_depth, including distinct zero-separation rows. Add each seed itself at distance zero. Seed concepts may be nonstandard or inactive; eligible targets must be active standard concepts.
3. Intersect ancestry across all distinct inputs, then apply same-domain, class and vocabulary eligibility. Filtering ancestors must never remove an input from the intersection requirement.
4. Rank each eligible common ancestor by the maximum of its stored minimum distances from all inputs. Exactly one nearest target is selected. Equal-distance targets remain ambiguous; concept ID sorting controls display order only.
5. Compute the outcome and total counts using the complete eligible common set, then paginate display candidates. `limit=1` cannot turn a tie into selection. Offset cannot affect the chosen target.

No silent query-row or node cap is permitted during selection. Enforce an input-size bound and the configured database statement timeout. If completion fails, return an execution error with no selected ancestor. `selection_complete` must never be true for a partial computation. A display page may be truncated while the selection calculation remains complete; keep these concepts distinct.

## Typed MCP response

Use a single object response with typed fields and typed `data`, retaining compatibility with the server's existing flat error dictionaries:

```python
class SharedAncestorResponse(BaseModel):
    error: bool = False
    code: ErrorCode | None = None
    message: str | None = None
    data: SharedAncestorData | None = None
```

Validate the envelope: on success data is required and code/message are absent; on error code/message are required, data is null, and no selection is exposed. Existing guarded error dictionaries `{error: true, code, message}` must validate through this model. The output model should not permit arbitrary dictionaries for the ancestry payload.

Important SDK constraint: a local FastMCP spike showed that annotating a return as `Success | Failure` automatically introduces `structuredContent.result`, while text content renders the unwrapped object. Use one object response so text and structured forms have the same documented shape. Add a contract test for this actual SDK behavior rather than assuming the return annotation alone fixes the wire contract. A second spike through GroundworkersMCPServer confirmed the single model works, but a guarded error dictionary omits null/default fields from text while structuredContent adds them. Normalize guarded results through the declared response model before returning to FastMCP so both content forms serialize the same payload. Ensure errors still pass through message scrubbing.

Successful example (candidate/page rows shortened for readability):

```json
{
  "error": false,
  "code": null,
  "message": null,
  "data": {
    "status": "selected",
    "found": true,
    "input_concept_ids": [40166038, 41038881],
    "constraints": {
      "concept_class_ids": ["Clinical Drug"],
      "vocabulary_ids": ["RxNorm", "RxNorm Extension"],
      "standard_only": true,
      "active_only": true,
      "same_domain": true
    },
    "ancestry_source": "concept_ancestor",
    "distance_metric": "min_levels_of_separation",
    "max_depth": 5,
    "include_self": true,
    "selection_complete": true,
    "selection_reason": "unique_nearest_within_constraints",
    "selected_ancestor": {
      "concept_id": 40166037,
      "concept_name": "saxagliptin 2.5 MG Oral Tablet",
      "vocabulary_id": "RxNorm",
      "domain_id": "Drug",
      "concept_class_id": "Clinical Drug",
      "standard_concept": true,
      "is_active": true,
      "depth": 1
    },
    "selected_distances_by_input": {"40166038": 0, "41038881": 1},
    "common_ancestors": [{
      "concept_id": 40166037,
      "concept_name": "saxagliptin 2.5 MG Oral Tablet",
      "vocabulary_id": "RxNorm",
      "domain_id": "Drug",
      "concept_class_id": "Clinical Drug",
      "standard_concept": true,
      "is_active": true,
      "depth": 1
    }],
    "common_candidate_count": 1,
    "nearest_candidate_count": 1,
    "page": {"offset": 0, "limit": 10, "returned": 1, "has_more": false}
  }
}
```

Each candidate uses the existing flags concept projection plus depth; the example abbreviates optional identity metadata. The selected concept must be included independently of pagination. Per-input distances for the selected result are bounded by the 200-input cap; alternatives remain compact. Preserve complete per-input evidence in replay artifacts when needed.

`status` is one of:

| Status | Meaning | found | selected_ancestor |
|---|---|---|---|
| selected | One unique nearest eligible ancestor | true | required |
| ambiguous | Multiple equally nearest eligible ancestors | false | null |
| no_match | No common eligible ancestor within the requested depth and constraints | false | null |
| incompatible_inputs | Inputs span different domains | false | null |

Unknown concepts, invalid requests and backend failures use the error envelope, never no_match. Stable machine reasons distinguish the statuses; an agent should not need to infer ambiguity from candidate counts. `common_candidate_count` and `nearest_candidate_count` always refer to full computed sets. `found` remains an alias for selection success for compatibility; it does not mean that common ancestry exists. In ambiguity, common ancestry exists despite found=false.

JSON Schema argument-validation failures may occur in the MCP SDK before the tool function runs. Treat these as native MCP tool errors (`isError=true`), distinct from the typed error envelope for requests that reached the service. Do not promise a typed payload for a protocol/schema error. Include both error routes in inference traces and wire-contract tests.

The MCP serializer can adapt the legacy Python service keys. Preserve existing plugin keys such as `selected_ancestor` and `selection_reason`; add fields rather than breaking those consumers. Document any intentional service validation change.

## Align existing tools

| Tool/path | Required change |
|---|---|
| concept_ancestors | Use canonical stored ancestry, exclude self by default, retain distinct zero-separation rows, allow max_depth=0, return the distance metric and effective scope. Preserve the existing ancestors list key. |
| concept_mapping_context | Use the same ancestor data. Keep ancestor_limit as a result-count limit; add ancestor_max_depth=5 independently. When ancestors are requested, require ancestor_limit=1..50 and depth=0..20. Echo count/depth/truncation separately. |
| concept_candidate_bundle hierarchy preview | Use canonical stored ancestry so previews do not contradict direct lookups. Report preview truncation. |
| concept_nearest_standard_ancestor | Use stored distances and active standard targets. Abstain on equal nearest distances instead of taking the first row. Preserve selected_parent and found keys; add explicit status/reason. Preserve direct Maps-to fallback as a separate mapping route with its own reason. |

The single-concept nearest tool accepts a query or concept_id. Ambiguity in selecting the source seed is a separate concern from ancestor ambiguity. For this release, retain and disclose the existing rank-1 seed policy and returned seed candidates; do not imply a unique source concept was established. A text query that selects a seed is not a deterministic complete clinical mapper.

`path_to_parent` traverses relationships whereas ancestor eligibility comes from stored closure. Failure to construct a bounded relationship path must not invalidate the ancestor-table evidence or fabricate a path. Return an empty path with an explicit explanation when those representations differ. Maps-to fallback must also be labelled as mapping, not ancestry.

Leave parent/child primitives and descendant traversal unchanged in this scoped release; document their separate semantics pending a dedicated descendant audit. The shared operation and read-only ancestor displays must be internally consistent. Document that ancestry includes zero-separation relations and does not prove interchangeability.

## MCP registration and annotations

Extend GroundworkersMCPServer.tool with optional keyword-only ToolAnnotations metadata and a result_model for typed output normalization; existing decorators remain valid. For the new tool, set result_model=SharedAncestorResponse; registration advertises that return type while the implementation returns an unvalidated dictionary for boundary validation. Resolve Annotated input aliases in the original tool namespace before FastMCP registration. After success or guarded error conversion, sanitize errors, validate through result_model and dump to JSON-compatible fields before FastMCP serialization. A malformed service result becomes a scrubbed INTERNAL error envelope, not a partial selection. This validation is opt-in for the new tool; do not silently change all existing tool payloads. Keep stored metadata alongside functions, expose it through describe_tools and forward it to FastMCP at run time. Mark the new operation readOnlyHint=true, destructiveHint=false, idempotentHint=true, openWorldHint=false. These are client hints, not authorization controls. Describe idempotence relative to an unchanged vocabulary snapshot.

Typed return annotations must survive wraps and error guarding. Test native MCP input validation, output validation, structuredContent and textContent; direct server.call tests alone are insufficient. The new tool stays in the existing mapping-service registration gate and has no embedding/LLM requirement.

## Implementation sequence and acceptance gates

1. Add typed schemas/validators and normalize the shared service result. Add MCP registration and annotation support. Verify the actual tools/list schema and success/error wire shapes.
2. Route ancestor displays/previews through the canonical stored primitive. Separate mapping-context result limits from depth. Preserve old result keys and document the two intentional changes: newly visible zero-distance ancestors and nearest-tool tie abstention.
3. Extend documentation, plugin examples and registration tests. Retain the upstream migration guide and keep SQL/ancestry retrieval independent of MCP types.
4. Replay deterministic fixed-seed cases, then run interactive sessions after restarting the MCP server and refreshing the client's tool list. Do not claim inference readiness before these steps pass.

Required regression scenarios: distinct zero-distance ancestor; explicit self; zero depth; mixed domains; inactive/nonstandard target; invalid and duplicated inputs; unknown filters; class-constrained tie resolution; empty constraint rejection; limit=1 preserving ambiguity; offset not changing selection; missing metadata/backend timeout returning an error; consistent ancestor presence across MCP tools; annotation and generated-schema assertions; split-vocabulary session routing. Compare constrained results with all 46 saved qualifying cases and preserve generic 29-selected/17-ambiguous behavior on that set.

## Interactive inference evaluation

Keep two distinct evaluations:

- Fixed candidate IDs exercise tool behavior and whether the agent understands selected, ambiguous and scoped no-match. The saved pilot requests contain no expected target IDs; the separate oracle is for evaluation only.
- Source-record prompts exercise end-to-end retrieval, candidate selection, class choice and mapping acceptance. Use the original source fields and hide historical references, expected concepts and adjudication labels from the agent.

Run the same model/settings/prompts against clearly versioned old/new toolsets. Save the advertised tool definitions, model/settings, source input, every tool argument/result, final target IDs, mapping grain and unresolved details. Record the vocabulary snapshot and Groundworkers source revision/hash. Restart the server and refresh client discovery between versions; stale tools/list metadata makes the comparison invalid.

Require the final agent answer to distinguish exact product, broad Clinical Drug, ingredient-only, ambiguous and abstained outcomes. Score ingredient precision/recall and exact-set agreement alongside form/strength/route/release/package correctness, abstentions and unsupported acceptance. A selected shared ancestor is tool evidence, not a correctness label. Review whether agents inappropriately treat zero distance as equivalence, omit dissenting candidates, merge separate ingredients, or overstate class-constrained uniqueness.

The existing 200-code cohort has already informed tuning. It remains useful for regression comparisons; an untouched sample is needed for a subsequent generalization claim. Interactive agent sessions also differ from the earlier vanilla rank-1 retrieval arm and must be labelled as a new workflow, with no implied apples-to-apples speed or clinical-accuracy ranking.

For an initial pilot, use the five saved probes (10128C, 10413C, 10714X, 13406N, 8881K). Expect four generic selections and one generic cross-class tie; all five should select the previously audited target under the Clinical Drug constraint. Add negative/source-discordant cases before estimating performance. Repeated agent runs can reveal tool-use variability even though these graph operations are deterministic for a fixed vocabulary.
