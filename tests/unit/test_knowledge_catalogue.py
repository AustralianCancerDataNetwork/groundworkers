from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from groundworkers.base.server import GroundcrewServer
from groundworkers.services.knowledge.catalogue import KnowledgeCatalogue
from groundworkers.services.knowledge.models import PackApplicability
from groundworkers.tools.knowledge_tools import register_knowledge_tools


def _write_pack(root: Path, layer: str, name: str, manifest_yaml: str, *, guidance=None, rules=None) -> None:
    pack_dir = root / layer / name
    pack_dir.mkdir(parents=True)
    (pack_dir / "manifest.yaml").write_text(manifest_yaml, encoding="utf-8")
    if guidance is not None:
        (pack_dir / "guidance.md").write_text(guidance, encoding="utf-8")
    if rules is not None:
        (pack_dir / "rules.yaml").write_text(rules, encoding="utf-8")


def _build_packs(root: Path) -> None:
    _write_pack(
        root,
        "core",
        "standard-concept-preference",
        'name: standard-concept-preference\nlayer: core\nversion: "1.0"\n'
        "shareability: public\nscope_summary: Prefer standard concepts.\n"
        "mechanisms:\n  - post-filter\napplicability:\n  always: true\n",
        guidance="# Standard Concept Selection\n\nUse standard_concept = 'S'.\n",
        rules="standard_concept_required:\n  reject_if: standard_concept != 'S'\n",
    )
    _write_pack(
        root,
        "source",
        "redcap-v1",
        'name: redcap-v1\nlayer: source\nversion: "1.0"\n'
        "shareability: public\nscope_summary: REDCap structural knowledge.\n"
        "mechanisms:\n  - context-inject\napplicability:\n  source_system: redcap\n",
    )
    _write_pack(
        root,
        "specialisation",
        "drug-pack",
        'name: drug-pack\nlayer: specialisation\nversion: "1.0"\n'
        "shareability: public\nscope_summary: Drug granularity.\n"
        "mechanisms:\n  - context-inject\napplicability:\n  domains:\n    - Drug\n",
    )


# ---------------------------------------------------------------------------
# Discovery / query
# ---------------------------------------------------------------------------

def test_catalogue_discovers_all_packs(tmp_path):
    _build_packs(tmp_path)
    catalogue = KnowledgeCatalogue(tmp_path)

    names = {m.name for m in catalogue.all()}

    assert names == {"standard-concept-preference", "redcap-v1", "drug-pack"}


def test_query_filters_by_source_system_but_always_keeps_core(tmp_path):
    _build_packs(tmp_path)
    catalogue = KnowledgeCatalogue(tmp_path)

    names = {m.name for m in catalogue.query(source_system="redcap")}

    # always=True core pack is included; redcap source pack matches; drug pack
    # (domains constraint, no domains supplied) is not filtered out on that axis.
    assert "standard-concept-preference" in names
    assert "redcap-v1" in names


def test_query_excludes_non_matching_source_system(tmp_path):
    _build_packs(tmp_path)
    catalogue = KnowledgeCatalogue(tmp_path)

    names = {m.name for m in catalogue.query(source_system="qualtrics")}

    assert "redcap-v1" not in names
    assert "standard-concept-preference" in names  # always=True


# ---------------------------------------------------------------------------
# matches() — regression coverage for the collapsed logic
# ---------------------------------------------------------------------------

def test_matches_always_true_ignores_all_context():
    app = PackApplicability(always=True, source_system="redcap")
    assert app.matches(source_system="qualtrics") is True


def test_matches_source_system_excludes_on_mismatch_includes_on_match_and_discovery():
    app = PackApplicability(source_system="redcap")
    assert app.matches(source_system="redcap") is True
    assert app.matches(source_system="qualtrics") is False
    assert app.matches() is True  # discovery mode: no context on the constrained axis


def test_matches_domains_any_overlap():
    app = PackApplicability(domains=["Drug", "Measurement"])
    assert app.matches(domains=["Drug"]) is True
    assert app.matches(domains=["Condition"]) is False
    assert app.matches(domains=None) is True


def test_matches_section_name_patterns_regex():
    app = PackApplicability(section_name_patterns=[r"phq-?9"])
    assert app.matches(section_names=["PHQ9 total"]) is True
    assert app.matches(section_names=["Demographics"]) is False


# ---------------------------------------------------------------------------
# get_pack — content serving (the finished feature)
# ---------------------------------------------------------------------------

def test_get_pack_serves_guidance_text_and_parsed_rules(tmp_path):
    _build_packs(tmp_path)
    catalogue = KnowledgeCatalogue(tmp_path)

    content = catalogue.get_pack("standard-concept-preference")

    assert content is not None
    assert content.manifest.name == "standard-concept-preference"
    assert "Standard Concept Selection" in content.guidance
    assert content.rules == {"standard_concept_required": {"reject_if": "standard_concept != 'S'"}}
    assert content.examples is None  # no examples.yaml present


def test_get_pack_missing_files_leave_none(tmp_path):
    _build_packs(tmp_path)
    catalogue = KnowledgeCatalogue(tmp_path)

    content = catalogue.get_pack("redcap-v1")

    assert content is not None
    assert content.guidance is None
    assert content.rules is None


def test_get_pack_unknown_returns_none(tmp_path):
    _build_packs(tmp_path)
    catalogue = KnowledgeCatalogue(tmp_path)

    assert catalogue.get_pack("does-not-exist") is None


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------

def test_knowledge_pack_tool_returns_content(tmp_path):
    _build_packs(tmp_path)
    server = GroundcrewServer("test")
    assert register_knowledge_tools(server, packs_root=tmp_path) is True

    result = server.call("knowledge_pack", "standard-concept-preference")

    assert result["name"] == "standard-concept-preference"
    assert "Standard Concept Selection" in result["guidance"]
    assert result["rules"]["standard_concept_required"]["reject_if"] == "standard_concept != 'S'"
    assert "guidance.md" in result["files_present"]


def test_knowledge_pack_tool_not_found(tmp_path):
    _build_packs(tmp_path)
    server = GroundcrewServer("test")
    register_knowledge_tools(server, packs_root=tmp_path)

    result = server.call("knowledge_pack", "nope")

    assert result["error"] is True
    assert result["code"] == "NOT_FOUND"


def test_register_knowledge_tools_absent_root_returns_false(tmp_path):
    server = GroundcrewServer("test")
    assert register_knowledge_tools(server, packs_root=tmp_path / "missing") is False
    assert "knowledge_pack" not in server.list_tools()
