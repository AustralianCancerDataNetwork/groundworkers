"""Registry prompt packs shipped with Groundworkers, and the contracts they restate."""

from __future__ import annotations

import copy
import json
import tomllib
from importlib.metadata import EntryPoint
from importlib.resources import files
from pathlib import Path

import pytest
import yaml

from groundworkers.services.source_planning import COLUMN_ROLE_DESCRIPTIONS, ColumnRole

COLUMN_PACK = "groundcrew-source-column-classification"
PACK_DIR = files("groundworkers.prompt_packs") / COLUMN_PACK


def _json(name: str) -> dict:
    return json.loads((PACK_DIR / name).read_text(encoding="utf-8"))


def test_entry_point_publishes_the_pack_directory() -> None:
    configuration = tomllib.loads(
        (Path(__file__).parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    )

    assert configuration["project"]["entry-points"]["ohdsi_prompt_registry.packs"] == {
        "groundworkers": "groundworkers.prompt_packs"
    }
    assert (PACK_DIR / "manifest.yaml").is_file()


def test_role_catalogue_matches_column_roles() -> None:
    catalogue = yaml.safe_load((PACK_DIR / "role-catalogue.yaml").read_text(encoding="utf-8"))

    assert catalogue == {role.value: text for role, text in COLUMN_ROLE_DESCRIPTIONS.items()}


@pytest.mark.parametrize(
    "schema_file", ["classification-request.schema.json", "classification-result.schema.json"]
)
def test_schema_role_enums_match_column_roles(schema_file: str) -> None:
    roles = _json(schema_file)["$defs"]["role"]["enum"]

    assert roles == [role.value for role in ColumnRole]


def _service():
    pytest.importorskip("ohdsi_prompt_registry")
    from ohdsi_prompt_registry.catalogue import PromptPackCatalogue
    from ohdsi_prompt_registry.service import PromptRegistryService
    from ohdsi_prompt_registry.sources import InstalledSource

    entry_point = EntryPoint(
        name="groundworkers",
        value="groundworkers.prompt_packs",
        group="ohdsi_prompt_registry.packs",
    )
    source = InstalledSource(provider=lambda **_kwargs: [entry_point])
    return PromptRegistryService(PromptPackCatalogue([source]))


def test_registry_loads_the_pack_and_its_examples_validate() -> None:
    service = _service()
    pack = service.registry.require(COLUMN_PACK)
    examples = service.document(COLUMN_PACK, "examples")
    assert isinstance(examples, dict)

    assert service.registry.rejections == []
    assert pack.source == "installed:groundworkers"
    assert pack.manifest.requires_tools == ["source_plan", "source_plan_assisted"]
    assert [prompt.name for prompt in service.prompts()] == ["gsc_classify_columns"]
    for key in ("classification_request", "classification_result"):
        assert service.validate(COLUMN_PACK, key, examples[key])["valid"], key

    prompt = service.prompt_text(
        COLUMN_PACK,
        "classify_columns",
        {"classification_request": json.dumps(examples["classification_request"])},
    )
    assert "ohdsi_prompt_registry_validate" in prompt


def test_result_contract_preserves_uncertainty_and_packed_value_semantics() -> None:
    service = _service()
    result = service.document(COLUMN_PACK, "examples")["classification_result"]

    invented_role = copy.deepcopy(result)
    invented_role["decisions"][0]["role"] = "primary_key"
    uncertain_with_high_confidence = copy.deepcopy(result)
    uncertain_with_high_confidence["decisions"][3]["confidence"] = 0.9
    packed_non_values = copy.deepcopy(result)
    packed_non_values["decisions"][0]["packed_value"] = True

    for invalid in (invented_role, uncertain_with_high_confidence, packed_non_values):
        assert not service.validate(COLUMN_PACK, "classification_result", invalid)["valid"]
