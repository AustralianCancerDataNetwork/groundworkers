"""Author checks for the Drug knowledge pack's structured reference data."""

from importlib.resources import files

import pytest
import yaml

from groundworkers.services.knowledge.catalogue import KnowledgeCatalogue

CORE_PACKS = files("groundworkers") / "knowledge" / "packs" / "core"


def test_drug_pack_class_criteria_reference_defined_attributes():
    pack = CORE_PACKS / "drug-concept-granularity"
    rules = yaml.safe_load((pack / "rules.yaml").read_text(encoding="utf-8"))
    defined = set(rules["attribute_definitions"])

    assert rules["concept_class_criteria"]
    for criterion in rules["concept_class_criteria"].values():
        attributes = criterion["defining_attributes"]
        assert set(attributes) <= defined
        assert len(attributes) == len(set(attributes))


@pytest.mark.parametrize("include_examples", [False, True])
def test_drug_pack_loads_with_optional_snapshot_examples(tmp_path, include_examples):
    source = CORE_PACKS / "drug-concept-granularity"
    destination = tmp_path / "core" / "drug-concept-granularity"
    destination.mkdir(parents=True)
    filenames = ["manifest.yaml", "guidance.md", "rules.yaml"]
    if include_examples:
        filenames.append("examples.yaml")
    for filename in filenames:
        (destination / filename).write_text(
            (source / filename).read_text(encoding="utf-8"), encoding="utf-8"
        )

    content = KnowledgeCatalogue(tmp_path).get_pack("drug-concept-granularity")

    assert content is not None
    assert content.guidance
    assert content.rules["concept_class_criteria"]
    if include_examples:
        assert content.examples["snapshot"]["provenance"]
        assert content.examples["snapshot_candidates"]
        assert content.examples["reasoning_scenarios"]
    else:
        assert content.examples is None


@pytest.mark.parametrize(
    "name", ["drug-concept-granularity", "standard-concept-preference"]
)
def test_drug_pack_references_exist_in_bundled_baseline(name):
    manifest = yaml.safe_load(
        (CORE_PACKS / name / "manifest.yaml").read_text(encoding="utf-8")
    )
    bundled = {
        yaml.safe_load(candidate.read_text(encoding="utf-8"))["name"]
        for candidate in CORE_PACKS.parent.glob("*/*/manifest.yaml")
    }

    assert set(manifest.get("see_also", [])) <= bundled
