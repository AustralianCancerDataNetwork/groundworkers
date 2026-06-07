from pathlib import Path
import sys
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest

from groundworkers.base.errors import GroundworkersError
from groundworkers.services.text import (
    DecomposeResult,
    DisambiguateResult,
    NormalizeResult,
    TextService,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _service(complete_structured_return=None, raises=None) -> TextService:
    llm = MagicMock()
    if raises:
        llm.complete_structured.side_effect = raises
    else:
        llm.complete_structured.return_value = complete_structured_return
    return TextService(llm)


# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------

def test_normalize_returns_typed_result():
    svc = _service({"normalized": "myocardial infarction", "original": "MI", "confidence": "high", "notes": None})
    result = svc.normalize("MI")
    assert isinstance(result, NormalizeResult)
    assert result.normalized == "myocardial infarction"
    assert result.confidence == "high"


def test_normalize_empty_text_raises_value_error():
    svc = _service({"normalized": "x", "original": "x", "confidence": "high"})
    with pytest.raises(ValueError, match="non-empty"):
        svc.normalize("   ")


def test_normalize_backend_unavail_propagates():
    svc = _service(raises=GroundworkersError("BACKEND_UNAVAIL", "API down"))
    with pytest.raises(GroundworkersError) as exc_info:
        svc.normalize("MI")
    assert exc_info.value.code == "BACKEND_UNAVAIL"


def test_normalize_schema_mismatch_raises_query_error():
    svc = _service({"unexpected": "shape"})
    with pytest.raises(GroundworkersError) as exc_info:
        svc.normalize("MI")
    assert exc_info.value.code == "QUERY_ERROR"


def test_normalize_domain_hint_in_prompt():
    llm = MagicMock()
    llm.complete_structured.return_value = {
        "normalized": "myocardial infarction", "original": "MI", "confidence": "high", "notes": None,
    }
    svc = TextService(llm)
    svc.normalize("MI", domain_hint="Condition")
    prompt = llm.complete_structured.call_args[0][0]
    assert "Condition" in prompt


def test_normalize_model_name_forwarded():
    llm = MagicMock()
    llm.complete_structured.return_value = {
        "normalized": "myocardial infarction", "original": "MI", "confidence": "high", "notes": None,
    }
    svc = TextService(llm)
    svc.normalize("MI", model_name="gpt-4o")
    assert llm.complete_structured.call_args[1]["model_name"] == "gpt-4o"


def test_normalize_system_prompt_contains_omop():
    llm = MagicMock()
    llm.complete_structured.return_value = {
        "normalized": "myocardial infarction", "original": "MI", "confidence": "high", "notes": None,
    }
    svc = TextService(llm)
    svc.normalize("MI")
    assert "OMOP" in llm.complete_structured.call_args[1]["system_prompt"]


# ---------------------------------------------------------------------------
# decompose
# ---------------------------------------------------------------------------

def test_decompose_returns_typed_result():
    svc = _service({
        "terms": [
            {"term": "type 2 diabetes mellitus", "domain_hint": "Condition"},
            {"term": "hypertension", "domain_hint": "Condition"},
        ],
        "original": "diabetic patients with high blood pressure",
    })
    result = svc.decompose("diabetic patients with high blood pressure")
    assert isinstance(result, DecomposeResult)
    assert len(result.terms) == 2
    assert result.terms[0].term == "type 2 diabetes mellitus"


def test_decompose_empty_text_raises_value_error():
    svc = _service({"terms": [], "original": "x"})
    with pytest.raises(ValueError):
        svc.decompose("")


def test_decompose_max_terms_clamped_in_prompt():
    llm = MagicMock()
    llm.complete_structured.return_value = {"terms": [], "original": "x"}
    svc = TextService(llm)
    svc.decompose("some text", max_terms=50)
    prompt = llm.complete_structured.call_args[0][0]
    assert "20" in prompt
    assert "50" not in prompt


def test_decompose_max_terms_minimum_1():
    llm = MagicMock()
    llm.complete_structured.return_value = {"terms": [], "original": "x"}
    svc = TextService(llm)
    svc.decompose("some text", max_terms=0)
    prompt = llm.complete_structured.call_args[0][0]
    assert "1" in prompt


def test_decompose_backend_unavail_propagates():
    svc = _service(raises=GroundworkersError("BACKEND_UNAVAIL", "API down"))
    with pytest.raises(GroundworkersError) as exc_info:
        svc.decompose("diabetes and hypertension")
    assert exc_info.value.code == "BACKEND_UNAVAIL"


def test_decompose_schema_mismatch_raises_query_error():
    svc = _service({"wrong": "shape"})
    with pytest.raises(GroundworkersError) as exc_info:
        svc.decompose("some text")
    assert exc_info.value.code == "QUERY_ERROR"


# ---------------------------------------------------------------------------
# disambiguate
# ---------------------------------------------------------------------------

def test_disambiguate_returns_typed_result():
    svc = _service({
        "interpretations": [
            {"interpretation": "multiple sclerosis", "domain_hint": "Condition", "context_clues": None},
            {"interpretation": "mitral stenosis", "domain_hint": "Condition", "context_clues": None},
        ],
        "original": "MS",
        "is_ambiguous": True,
    })
    result = svc.disambiguate("MS")
    assert isinstance(result, DisambiguateResult)
    assert result.is_ambiguous is True
    assert len(result.interpretations) == 2


def test_disambiguate_unambiguous_term():
    svc = _service({
        "interpretations": [
            {"interpretation": "myocardial infarction", "domain_hint": "Condition", "context_clues": None},
        ],
        "original": "heart attack",
        "is_ambiguous": False,
    })
    result = svc.disambiguate("heart attack")
    assert result.is_ambiguous is False


def test_disambiguate_empty_text_raises_value_error():
    svc = _service({"interpretations": [], "original": "x", "is_ambiguous": False})
    with pytest.raises(ValueError):
        svc.disambiguate("")


def test_disambiguate_max_interpretations_clamped_to_10():
    llm = MagicMock()
    llm.complete_structured.return_value = {"interpretations": [], "original": "x", "is_ambiguous": False}
    svc = TextService(llm)
    svc.disambiguate("SOB", max_interpretations=100)
    prompt = llm.complete_structured.call_args[0][0]
    assert "10" in prompt
    assert "100" not in prompt


def test_disambiguate_backend_unavail_propagates():
    svc = _service(raises=GroundworkersError("BACKEND_UNAVAIL", "API down"))
    with pytest.raises(GroundworkersError) as exc_info:
        svc.disambiguate("MS")
    assert exc_info.value.code == "BACKEND_UNAVAIL"


def test_disambiguate_context_clues_optional():
    svc = _service({
        "interpretations": [{"interpretation": "shortness of breath", "domain_hint": "Observation"}],
        "original": "SOB",
        "is_ambiguous": False,
    })
    result = svc.disambiguate("SOB")
    assert result.interpretations[0].context_clues is None
