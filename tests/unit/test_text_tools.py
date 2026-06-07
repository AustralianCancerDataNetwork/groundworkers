from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from groundworkers.base.errors import GroundworkersError
from groundworkers.base.server import GroundcrewServer
from groundworkers.services.text import (
    DecomposeResult,
    DecomposeTerm,
    DisambiguateResult,
    Interpretation,
    NormalizeResult,
)
from groundworkers.tools.text_tools import register_text_tools


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
        raises: Exception | None = None,
    ) -> None:
        self._normalize = normalize_result
        self._decompose = decompose_result
        self._disambiguate = disambiguate_result
        self._raises = raises
        self.normalize_calls: list[dict] = []
        self.decompose_calls: list[dict] = []
        self.disambiguate_calls: list[dict] = []

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
