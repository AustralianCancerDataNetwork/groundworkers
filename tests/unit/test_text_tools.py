

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundcrewServer
from groundworkers.services.text import (
    DecomposeResult,
    DecomposeTerm,
    DisambiguateResult,
    Interpretation,
    MappingCleanupResult,
    NormalizeResult,
)
from groundworkers.services.text.prompts import SYSTEM_PROMPTS, build_user_prompt
from groundworkers.tools.text_tools import register_text_prompts, register_text_tools

# ---------------------------------------------------------------------------
# Stub service
# ---------------------------------------------------------------------------

class StubTextService:
    def __init__(
        self,
        *,
        normalize_result: NormalizeResult | None = None,
        decompose_result: DecomposeResult | None = None,
        disambiguate_result: DisambiguateResult | None = None,
        mapping_cleanup_result: MappingCleanupResult | None = None,
        raises: Exception | None = None,
    ) -> None:
        self._normalize = normalize_result
        self._decompose = decompose_result
        self._disambiguate = disambiguate_result
        self._mapping_cleanup = mapping_cleanup_result
        self._raises = raises
        self.normalize_calls: list[dict] = []
        self.decompose_calls: list[dict] = []
        self.disambiguate_calls: list[dict] = []
        self.mapping_cleanup_calls: list[dict] = []

    def normalize(self, text, *, domain_hint=None, model_name=None):
        self.normalize_calls.append({"text": text, "domain_hint": domain_hint, "model_name": model_name})
        if self._raises:
            raise self._raises
        return self._normalize

    def decompose(self, text, *, domain_hint=None, max_terms=10, model_name=None):
        self.decompose_calls.append({"text": text, "domain_hint": domain_hint, "max_terms": max_terms, "model_name": model_name})
        if self._raises:
            raise self._raises
        return self._decompose

    def disambiguate(self, text, *, domain_hint=None, max_interpretations=5, model_name=None):
        self.disambiguate_calls.append({"text": text, "domain_hint": domain_hint, "max_interpretations": max_interpretations, "model_name": model_name})
        if self._raises:
            raise self._raises
        return self._disambiguate

    def mapping_cleanup(self, text, *, context=None, domain_hint=None, model_name=None):
        self.mapping_cleanup_calls.append(
            {"text": text, "context": context, "domain_hint": domain_hint, "model_name": model_name}
        )
        if self._raises:
            raise self._raises
        return self._mapping_cleanup


def _server(service) -> GroundcrewServer:
    server = GroundcrewServer("test-server")
    register_text_tools(server, service)
    return server


# ---------------------------------------------------------------------------
# text_normalize
# ---------------------------------------------------------------------------

def test_text_normalize_returns_model_dump():
    svc = StubTextService(normalize_result=NormalizeResult(
        normalized="myocardial infarction", original="MI", confidence="high", notes=None,
    ))
    result = _server(svc).call("text_normalize", text="MI")
    assert result["normalized"] == "myocardial infarction"
    assert result["confidence"] == "high"
    assert result["original"] == "MI"
    assert "notes" in result


def test_text_normalize_invalid_input_from_service():
    svc = StubTextService(raises=ValueError("text must be a non-empty string"))
    result = _server(svc).call("text_normalize", text="   ")
    assert result["error"] is True
    assert result["code"] == "INVALID_INPUT"


def test_text_normalize_backend_unavail_surfaced():
    svc = StubTextService(raises=GroundworkersError("BACKEND_UNAVAIL", "API down"))
    result = _server(svc).call("text_normalize", text="MI")
    assert result["error"] is True
    assert result["code"] == "BACKEND_UNAVAIL"


def test_text_normalize_query_error_surfaced():
    svc = StubTextService(raises=GroundworkersError("QUERY_ERROR", "bad response"))
    result = _server(svc).call("text_normalize", text="MI")
    assert result["error"] is True
    assert result["code"] == "QUERY_ERROR"


def test_text_normalize_passes_domain_hint_and_model_name():
    svc = StubTextService(normalize_result=NormalizeResult(
        normalized="myocardial infarction", original="MI", confidence="high",
    ))
    _server(svc).call("text_normalize", text="MI", domain_hint="Condition", model_name="gpt-4o")
    call = svc.normalize_calls[0]
    assert call["domain_hint"] == "Condition"
    assert call["model_name"] == "gpt-4o"


# ---------------------------------------------------------------------------
# text_mapping_cleanup
# ---------------------------------------------------------------------------

def test_text_mapping_cleanup_returns_model_dump():
    svc = StubTextService(mapping_cleanup_result=MappingCleanupResult(
        replacement="orientation difficulty with time relationships",
        original="Questionable = 0.5 Fully oriented except for slight difficulty with time relationships",
        changed=True,
        confidence="high",
        notes=None,
    ))
    result = _server(svc).call(
        "text_mapping_cleanup",
        text="Questionable = 0.5 Fully oriented except for slight difficulty with time relationships",
    )
    assert result["changed"] is True
    assert result["replacement"] == "orientation difficulty with time relationships"


def test_text_mapping_cleanup_invalid_input_from_service():
    svc = StubTextService(raises=ValueError("text must be a non-empty string"))
    result = _server(svc).call("text_mapping_cleanup", text="")
    assert result["error"] is True
    assert result["code"] == "INVALID_INPUT"


def test_text_mapping_cleanup_passes_context_domain_and_model_name():
    svc = StubTextService(mapping_cleanup_result=MappingCleanupResult(
        replacement="orientation difficulty with time relationships",
        original="Questionable = 0.5 Fully oriented except for slight difficulty with time relationships",
        changed=True,
        confidence="high",
        notes=None,
    ))
    _server(svc).call(
        "text_mapping_cleanup",
        text="Questionable = 0.5 Fully oriented except for slight difficulty with time relationships",
        context={"parent_label": "2. Orientation"},
        domain_hint="Measurement",
        model_name="gpt-4o",
    )
    call = svc.mapping_cleanup_calls[0]
    assert call["context"] == {"parent_label": "2. Orientation"}
    assert call["domain_hint"] == "Measurement"
    assert call["model_name"] == "gpt-4o"


# ---------------------------------------------------------------------------
# text_decompose
# ---------------------------------------------------------------------------

def test_text_decompose_returns_model_dump():
    svc = StubTextService(decompose_result=DecomposeResult(
        terms=[
            DecomposeTerm(term="type 2 diabetes mellitus", domain_hint="Condition"),
            DecomposeTerm(term="hypertension", domain_hint="Condition"),
        ],
        original="diabetic patients with high blood pressure",
    ))
    result = _server(svc).call("text_decompose", text="diabetic patients with high blood pressure")
    assert len(result["terms"]) == 2
    assert result["terms"][0]["term"] == "type 2 diabetes mellitus"
    assert result["original"] == "diabetic patients with high blood pressure"


def test_text_decompose_invalid_input_from_service():
    svc = StubTextService(raises=ValueError("text must be a non-empty string"))
    result = _server(svc).call("text_decompose", text="")
    assert result["error"] is True
    assert result["code"] == "INVALID_INPUT"


def test_text_decompose_backend_unavail_surfaced():
    svc = StubTextService(raises=GroundworkersError("BACKEND_UNAVAIL", "API down"))
    result = _server(svc).call("text_decompose", text="some text")
    assert result["error"] is True
    assert result["code"] == "BACKEND_UNAVAIL"


def test_text_decompose_passes_max_terms():
    svc = StubTextService(decompose_result=DecomposeResult(terms=[], original="x"))
    _server(svc).call("text_decompose", text="some text", max_terms=15)
    assert svc.decompose_calls[0]["max_terms"] == 15


# ---------------------------------------------------------------------------
# text_disambiguate
# ---------------------------------------------------------------------------

def test_text_disambiguate_returns_model_dump():
    svc = StubTextService(disambiguate_result=DisambiguateResult(
        interpretations=[
            Interpretation(interpretation="multiple sclerosis", domain_hint="Condition"),
            Interpretation(interpretation="mitral stenosis", domain_hint="Condition"),
        ],
        original="MS",
        is_ambiguous=True,
    ))
    result = _server(svc).call("text_disambiguate", text="MS")
    assert result["is_ambiguous"] is True
    assert len(result["interpretations"]) == 2
    assert result["original"] == "MS"


def test_text_disambiguate_unambiguous_term():
    svc = StubTextService(disambiguate_result=DisambiguateResult(
        interpretations=[Interpretation(interpretation="myocardial infarction", domain_hint="Condition")],
        original="heart attack",
        is_ambiguous=False,
    ))
    result = _server(svc).call("text_disambiguate", text="heart attack")
    assert result["is_ambiguous"] is False
    assert len(result["interpretations"]) == 1


def test_text_disambiguate_invalid_input_from_service():
    svc = StubTextService(raises=ValueError("text must be a non-empty string"))
    result = _server(svc).call("text_disambiguate", text="")
    assert result["error"] is True
    assert result["code"] == "INVALID_INPUT"


def test_text_disambiguate_passes_max_interpretations():
    svc = StubTextService(disambiguate_result=DisambiguateResult(interpretations=[], original="x", is_ambiguous=False))
    _server(svc).call("text_disambiguate", text="SOB", max_interpretations=3)
    assert svc.disambiguate_calls[0]["max_interpretations"] == 3


# ---------------------------------------------------------------------------
# register_text_prompts
# ---------------------------------------------------------------------------

def _prompt_server() -> GroundcrewServer:
    server = GroundcrewServer("test-server")
    register_text_prompts(server)
    return server


def test_register_text_prompts_registers_three_prompts():
    server = _prompt_server()
    assert set(server.list_prompts()) == {
        "normalize_clinical_term",
        "cleanup_mapping_text",
        "decompose_clinical_text",
        "disambiguate_clinical_term",
    }


def test_normalize_prompt_returns_single_user_message():
    result = _prompt_server().call_prompt("normalize_clinical_term", text="MI")
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["role"] == "user"


def test_normalize_prompt_content_contains_text():
    result = _prompt_server().call_prompt("normalize_clinical_term", text="MI")
    assert "'MI'" in result[0]["content"]["text"]


def test_normalize_prompt_system_folded_into_user_content():
    result = _prompt_server().call_prompt("normalize_clinical_term", text="MI")
    assert SYSTEM_PROMPTS["normalize"] in result[0]["content"]["text"]


def test_normalize_prompt_domain_hint_flows_through():
    result = _prompt_server().call_prompt("normalize_clinical_term", text="MS", domain_hint="Condition")
    assert "Condition" in result[0]["content"]["text"]


def test_normalize_prompt_empty_domain_hint_shows_not_specified():
    result = _prompt_server().call_prompt("normalize_clinical_term", text="MI", domain_hint="")
    assert "not specified" in result[0]["content"]["text"]


def test_cleanup_mapping_prompt_contains_context():
    result = _prompt_server().call_prompt(
        "cleanup_mapping_text",
        text="Questionable = 0.5 Fully oriented except for slight difficulty with time relationships",
        domain_hint="Measurement",
        context={"parent_label": "2. Orientation"},
    )
    assert "Measurement" in result[0]["content"]["text"]
    assert "parent_label" in result[0]["content"]["text"]


def test_decompose_prompt_returns_single_user_message():
    result = _prompt_server().call_prompt("decompose_clinical_text", text="T2DM and HTN on metformin")
    assert len(result) == 1
    assert result[0]["role"] == "user"


def test_decompose_prompt_max_terms_default_in_content():
    result = _prompt_server().call_prompt("decompose_clinical_text", text="some phrase")
    assert "10" in result[0]["content"]["text"]


def test_decompose_prompt_max_terms_clamped_to_20():
    result = _prompt_server().call_prompt("decompose_clinical_text", text="some phrase", max_terms=50)
    assert "20" in result[0]["content"]["text"]
    assert "50" not in result[0]["content"]["text"]


def test_decompose_prompt_max_terms_clamped_to_1():
    result = _prompt_server().call_prompt("decompose_clinical_text", text="some phrase", max_terms=0)
    assert "1" in result[0]["content"]["text"]


def test_disambiguate_prompt_returns_single_user_message():
    result = _prompt_server().call_prompt("disambiguate_clinical_term", text="MS")
    assert len(result) == 1
    assert result[0]["role"] == "user"


def test_disambiguate_prompt_max_interpretations_clamped_to_10():
    result = _prompt_server().call_prompt("disambiguate_clinical_term", text="MS", max_interpretations=15)
    assert "10" in result[0]["content"]["text"]
    assert "15" not in result[0]["content"]["text"]


def test_disambiguate_prompt_max_interpretations_default_in_content():
    result = _prompt_server().call_prompt("disambiguate_clinical_term", text="MS")
    assert "5" in result[0]["content"]["text"]


def test_prompt_and_service_use_same_user_turn():
    """normalize prompt content matches what build_user_prompt produces directly."""
    text = "DM2"
    domain = "Condition"
    prompt_result = _prompt_server().call_prompt("normalize_clinical_term", text=text, domain_hint=domain)
    expected_user = build_user_prompt("normalize", text, domain_hint=domain)
    assert expected_user in prompt_result[0]["content"]["text"]


def test_decompose_prompt_and_service_use_same_user_turn():
    text = "T2DM and HTN"
    prompt_result = _prompt_server().call_prompt("decompose_clinical_text", text=text, max_terms=7)
    expected_user = build_user_prompt("decompose", text, max_terms=7)
    assert expected_user in prompt_result[0]["content"]["text"]
