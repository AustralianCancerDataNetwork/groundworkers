from pathlib import Path
import os
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from groundworkers.base.errors import GroundworkersError
from groundworkers.config import AppConfig
from groundworkers.server import build_adapters


def _load_integration_adapter():
    try:
        import omop_emb  # noqa: F401
    except ImportError:
        pytest.skip("omop_emb is not installed in this environment")

    config_path = os.getenv("GROUNDWORKERS_CONFIG", "config/groundworkers.example.yaml")
    config = AppConfig.load(config_path)
    if config.omop_emb is None or not config.omop_emb.enabled:
        pytest.skip("omop_emb is not enabled in the selected config")
    adapter = build_adapters(config).omop_emb
    if adapter is None:
        pytest.skip("omop_emb adapter was not built")
    status = adapter.index_status()
    if not status["available"]:
        pytest.skip("omop_emb backend is not available in this environment")
    return adapter


@pytest.mark.integration
def test_index_status_returns_at_least_one_model():
    adapter = _load_integration_adapter()

    status = adapter.index_status()

    assert status["available"] is True
    assert status["models"]


def _get_test_concept_id() -> int:
    value = os.getenv("GROUNDWORKERS_TEST_CONCEPT_ID")
    if not value:
        pytest.skip("GROUNDWORKERS_TEST_CONCEPT_ID env var not set")
    return int(value)


@pytest.mark.integration
def test_get_neighbours_returns_sorted_results():
    adapter = _load_integration_adapter()
    status = adapter.index_status()
    concept_id = _get_test_concept_id()

    result = adapter.get_neighbours(
        concept_id=concept_id,
        limit=10,
        model_name=status["models"][0]["model_name"],
    )

    similarities = [item["similarity"] for item in result["results"]]
    assert similarities == sorted(similarities, reverse=True)


@pytest.mark.integration
def test_get_neighbours_excludes_query_concept():
    adapter = _load_integration_adapter()
    status = adapter.index_status()
    concept_id = _get_test_concept_id()

    result = adapter.get_neighbours(
        concept_id=concept_id,
        limit=10,
        model_name=status["models"][0]["model_name"],
    )

    assert all(item["concept_id"] != concept_id for item in result["results"])


@pytest.mark.integration
def test_get_neighbours_unknown_concept_raises_not_found():
    adapter = _load_integration_adapter()
    status = adapter.index_status()

    with pytest.raises(GroundworkersError) as excinfo:
        adapter.get_neighbours(
            concept_id=999999999,
            limit=10,
            model_name=status["models"][0]["model_name"],
        )

    assert excinfo.value.code == "NOT_FOUND"
