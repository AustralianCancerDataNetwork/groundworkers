# TextService

`TextService` provides LLM-backed clinical text preprocessing. Its methods normalize
ambiguous clinical terms, decompose free text into discrete search terms, and return
ranked interpretations of ambiguous phrases. The results are designed as inputs to the
concept grounding pipeline rather than as standalone outputs.

## Construction

`TextService` requires a configured `LLMAdapter`. It is constructed automatically by
`build_application()` when `llm` is configured.

```python
from groundworkers.app import build_application
from groundworkers.config import AppConfig

config = AppConfig.load("config/groundworkers.local.yaml")
app = build_application(config)

text = app.services.text
```

`text` is `None` when `llm` is not configured.

## Methods

### `normalize`

```python
text.normalize(
    text: str,
    *,
    domain_hint: str | None = None,
    model_name: str | None = None,
) -> NormalizeResult
```

Converts a clinical term, abbreviation, or lay phrase into OMOP-compatible form.
For example, `"DM2"` → `"Type 2 diabetes mellitus"`, or `"high BP"` →
`"Hypertension"`.

The `domain_hint` parameter guides the model toward a specific OMOP domain
(e.g. `"Condition"`, `"Drug"`, `"Measurement"`), which reduces ambiguity when the
same phrase has different meanings across domains.

`model_name` overrides the `default_model_name` from the LLM config for this call.

### `decompose`

```python
text.decompose(
    text: str,
    *,
    domain_hint: str | None = None,
    max_terms: int | None = None,
    model_name: str | None = None,
) -> DecomposeResult
```

Splits a free-text clinical phrase into a list of normalized, individually searchable
terms. For example, `"patient with T2DM and HTN on metformin"` decomposes into
`["Type 2 diabetes mellitus", "Hypertension", "Metformin"]`.

Each returned term includes a `domain_hint` inferred by the model, which can be passed
directly into search or grounding tool calls.

### `disambiguate`

```python
text.disambiguate(
    text: str,
    *,
    domain_hint: str | None = None,
    max_interpretations: int | None = None,
    model_name: str | None = None,
) -> DisambiguateResult
```

Returns ranked candidate interpretations of an ambiguous term. For example, `"MS"`
might return `"Multiple sclerosis"`, `"Mitral stenosis"`, and `"Mass spectrometry"` as
distinct interpretations, each with a `domain_hint` and supporting `context_clues`.

`is_ambiguous` in the result is `True` when the model identified more than one
plausible interpretation. A result with `is_ambiguous=False` and a single
interpretation behaves the same as a normalized result.

## Return types

### `NormalizeResult`

```python
@dataclass
class NormalizeResult:
    normalized: str       # OMOP-compatible normalized form
    original: str         # the input text unchanged
    confidence: float     # model's self-reported confidence in [0.0, 1.0]
    notes: str | None     # optional model commentary on ambiguity or alternatives
```

### `DecomposeResult`

```python
@dataclass
class DecomposeResult:
    terms: list[DecomposedTerm]
    original: str

@dataclass
class DecomposedTerm:
    term: str             # normalized OMOP-compatible form
    domain_hint: str | None
```

### `DisambiguateResult`

```python
@dataclass
class DisambiguateResult:
    interpretations: list[Interpretation]
    original: str
    is_ambiguous: bool

@dataclass
class Interpretation:
    interpretation: str   # normalized OMOP-compatible form
    domain_hint: str | None
    context_clues: list[str]  # phrases or signals that support this interpretation
```

## Prompts

Prompts for each method are versioned with the service code in the `services/`
directory. They are not stored in YAML config or in MCP prompt metadata. This keeps
prompt changes traceable through the same version history as the service logic, and
ensures that a given version of `TextService` produces predictable outputs regardless
of external configuration.

## Error handling

- Raises `ValueError` for empty input strings (before calling the LLM).
- Raises `GroundworkersError(BACKEND_UNAVAIL)` when `LLMAdapter` is not configured
  or the LLM API is unreachable.
- Raises `GroundworkersError(QUERY_ERROR)` on model API errors or when the structured
  response cannot be parsed.
- Never returns error dicts — that is the tool layer's responsibility.

## Relationship to concept grounding

`TextService` methods are preprocessing steps, not replacements for concept grounding.
The typical flow is:

```
free text
  → TextService.decompose()      # split into discrete terms
  → TextService.normalize()      # normalize each term (if needed)
  → VocabService.search_*()      # lexical candidates
  → MappingService.concept_candidate_bundle()  # multi-channel candidates
  → concept grounding / review
```

`disambiguate` is useful when the input is known to be ambiguous and the caller wants
to present multiple grounding paths to a reviewer rather than committing to one
interpretation automatically.
