"""The published domain-routing facts, and every copy that must match them."""

from __future__ import annotations

import re
from importlib.resources import files

import pytest
import yaml

from groundworkers.omop_domains import (
    CLASSIFIABLE_DOMAINS,
    DOMAIN_CLASSIFY_OUTPUTS,
    DOMAIN_TO_TABLE,
    NON_CLINICAL,
    table_for_domain,
)
from groundworkers.services import domain as domain_service

ROUTING_PACK = files("groundworkers") / "knowledge" / "packs" / "core" / "domain-routing"


def test_classifiable_domains_are_routed_domains() -> None:
    assert set(CLASSIFIABLE_DOMAINS) <= set(DOMAIN_TO_TABLE)


def test_non_clinical_labels_are_not_routed_domains() -> None:
    assert not set(NON_CLINICAL) & set(DOMAIN_TO_TABLE)
    assert DOMAIN_CLASSIFY_OUTPUTS == set(CLASSIFIABLE_DOMAINS) | set(NON_CLINICAL)


def test_routing_table_is_read_only() -> None:
    with pytest.raises(TypeError):
        DOMAIN_TO_TABLE["Episode"] = "EPISODE"  # type: ignore[index]


@pytest.mark.parametrize(
    ("domain_id", "table"),
    [("Condition", "CONDITION_OCCURRENCE"), ("Specimen", "SPECIMEN"), ("Metadata", None),
     ("Meas Value", None), (None, None)],
)
def test_table_for_domain(domain_id, table) -> None:
    assert table_for_domain(domain_id) == table


def test_knowledge_pack_rules_match_the_routing_table() -> None:
    rules = yaml.safe_load((ROUTING_PACK / "rules.yaml").read_text(encoding="utf-8"))

    assert rules["domain_to_table"] == dict(DOMAIN_TO_TABLE)


def test_knowledge_pack_guidance_table_matches_the_routing_table() -> None:
    guidance = (ROUTING_PACK / "guidance.md").read_text(encoding="utf-8")
    section = guidance.split("## Domain → CDM table mapping", 1)[1].split("\n## ", 1)[0]
    rows = re.findall(r"^\| (\w[\w ]*?) \| ([A-Z_]+) \|$", section, re.MULTILINE)

    assert dict(rows) == dict(DOMAIN_TO_TABLE)


def test_domain_classify_accepts_exactly_the_published_outputs() -> None:
    assert domain_service._VALID_DOMAINS == DOMAIN_CLASSIFY_OUTPUTS
    schema_enum = domain_service._DOMAIN_RESPONSE_SCHEMA["additionalProperties"]["oneOf"][0]["enum"]
    assert set(schema_enum) == DOMAIN_CLASSIFY_OUTPUTS


def test_domain_classify_prompt_describes_exactly_the_published_outputs() -> None:
    described = re.findall(r"^  (\w+)\s+—", domain_service._DOMAIN_SYSTEM, re.MULTILINE)

    assert set(described) == DOMAIN_CLASSIFY_OUTPUTS


def test_domain_classify_tool_description_lists_exactly_the_published_outputs() -> None:
    source = files("groundworkers.tools").joinpath("domain_tools.py").read_text(encoding="utf-8")
    listed = re.search(r"Valid domains: ([^.]+)\.", source)
    assert listed is not None
    names = {name.strip() for name in listed.group(1).replace("\n", " ").split(",")}

    assert names == DOMAIN_CLASSIFY_OUTPUTS
