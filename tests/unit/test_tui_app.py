from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from oa_configurator import save_stack_config

from tests.support.stack_config import build_cdm_stack, build_embedding_stack


def _write_embedding_stack(path: Path) -> None:
    save_stack_config(build_embedding_stack(), path)


def _embedding_configuration(path: str):
    from groundworkers.application.setup.embedding_setup import (
        load_embedding_configuration,
    )

    configuration = load_embedding_configuration(_snapshot_for(path))
    assert configuration is not None
    return configuration


def _snapshot_for(path: str):
    from groundworkers.application.setup.configuration import load_configuration

    return load_configuration(config_path=path)


def test_groundworkers_spec_keeps_setup_as_a_registered_page() -> None:
    pytest.importorskip("groundskeeping")

    from groundworkers.tui.app import build_groundworkers_tui_spec

    spec = build_groundworkers_tui_spec(
        config_path="/definitely/not/a/groundworkers-config.toml",
    )

    assert spec.validate().keys() == ("setup",)
    assert spec.default_page == "setup"
    assert spec.title == "Groundworkers"


def test_groundworkers_pages_do_not_cover_workbench(tmp_path: Path) -> None:
    """Layout only. Uses a real config so the page renders its sections.

    A missing config now opens the location wizard on activate, which is a
    different journey and is covered by its own tests.
    """
    pytest.importorskip("groundskeeping")

    from groundskeeping.app import OperatorApp

    from groundworkers.tui.app import build_groundworkers_tui_spec

    config_path = tmp_path / "config.toml"
    save_stack_config(build_cdm_stack(), config_path)

    async def run_check() -> None:
        app = OperatorApp(
            build_groundworkers_tui_spec(config_path=str(config_path))
        )

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()

            active_page = app.query_one(".operator-page.-active")
            workbench = app.query_one("#workbench")
            catalogue_panel = app.query_one("#catalogue-panel")
            sections = app.query_one("#sections")
            catalogue = app.query_one("#catalogue")
            tabs = app.query_one("#page-tabs")

            assert active_page.region.height == 0
            assert workbench.region.height > 0
            assert catalogue_panel.region.height > 0
            assert tabs.styles.display == "none"
            assert sections.styles.display == "block"
            assert catalogue.styles.display == "none"
            assert sections.option_count == 6
            assert app.query_one("#result-panel").border_title == "Setup"
            # A resolvable config lists its databases, so the context panel shows
            # the highlighted row's detail rather than the setup placeholder.
            assert app.query_one("#context-panel").border_title == "Database detail"
            assert tuple(
                sections.get_option_at_index(index).id
                for index in range(sections.option_count)
            ) == (
                "setup.database",
                "setup.graph",
                "setup.llm_provider",
                "setup.embeddings",
                "setup.chat",
                "setup.configuration",
            )

            sections.focus()
            sections.highlighted = 2
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert "LLM provider not configured" in str(
                app.query_one("#result-summary").render()
            )
            assert app.query_one("#result-panel").border_title == "Setup"
            assert app.query_one("#context-panel").border_title == "Model inventory"

            sections.highlighted = 1
            await pilot.press("enter")
            await pilot.pause()
            assert app.query_one("#result-panel").border_title == "Setup"
            assert app.query_one("#context-panel").border_title == "Graph Setup"
            assert app.query_one("#context").text == ""
            assert app.query_one("#context-table").styles.display == "none"

    asyncio.run(run_check())


def test_database_actions_render_in_workspace_and_verify_connections(
    tmp_path: Path,
) -> None:
    pytest.importorskip("groundskeeping")

    from groundskeeping.app import OperatorApp

    from groundworkers.tui.app import build_groundworkers_tui_spec

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[connections.main]
dialect = "sqlite"
database_name = ":memory:"

[databases.cdm_db]
kind = "cdm"
connection = "main"
schema_name = "main"
vocab_schema = "main"

[tools.groundworkers]
cdm_db = "cdm_db"
""",
        encoding="utf-8",
    )

    async def run_check() -> None:
        app = OperatorApp(build_groundworkers_tui_spec(config_path=str(config_path)))

        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            assert app.query_one("#catalogue").styles.display == "none"
            assert str(app.query_one("#view-action-0").label) == "Configure"
            assert str(app.query_one("#view-action-1").label) == "Test connections"
            assert str(app.query_one("#view-action-2").label) == "Refresh"

            await pilot.click("#view-action-1")
            await pilot.pause(0.2)

            table = app.query_one("#result-table")
            assert table.get_row_at(0)[3] == "Warnings"
            table.move_cursor(row=0, column=0, animate=False)
            await pilot.pause()
            context = app.query_one("#context-table")
            assert str(context.get_row_at(0)[0]) == "✓"
            assert str(context.get_row_at(1)[0]) == "!"
            assert "OMOP vocabulary tables are missing" in str(context.get_row_at(1)[1])

    asyncio.run(run_check())


def test_groundworkers_tuning_row_does_not_offer_configure(
    tmp_path: Path,
) -> None:
    pytest.importorskip("groundskeeping")

    from groundskeeping.app import OperatorApp

    from groundworkers.tui.app import build_groundworkers_tui_spec

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[connections.main]
dialect = "sqlite"
database_name = ":memory:"

[databases.cdm_db]
kind = "cdm"
connection = "main"
schema_name = "main"
vocab_schema = "main"

[tools.groundworkers]
cdm_db = "cdm_db"
""",
        encoding="utf-8",
    )

    async def run_check() -> None:
        app = OperatorApp(build_groundworkers_tui_spec(config_path=str(config_path)))

        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            table = app.query_one("#result-table")
            table.move_cursor(row=2, column=0, animate=False)
            await pilot.pause()

            assert table.get_row_at(2)[0] == "Groundworkers tuning"
            assert str(app.query_one("#view-action-0").label) == "Configure"
            assert app.query_one("#view-action-0").disabled is True
            assert str(app.query_one("#view-action-1").label) == "Test connections"
            assert str(app.query_one("#view-action-2").label) == "Refresh"

    asyncio.run(run_check())


def test_configure_action_opens_database_wizard(tmp_path: Path) -> None:
    pytest.importorskip("groundskeeping")

    from groundskeeping.app import OperatorApp

    from groundworkers.tui.app import build_groundworkers_tui_spec

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[connections.main]
dialect = "sqlite"
database_name = ":memory:"

[databases.cdm_db]
kind = "cdm"
connection = "main"
schema_name = "main"

[tools.groundworkers]
cdm_db = "cdm_db"
""",
        encoding="utf-8",
    )

    async def run_check() -> None:
        app = OperatorApp(build_groundworkers_tui_spec(config_path=str(config_path)))

        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            await pilot.click("#view-action-0")
            await pilot.pause()

            assert app.screen.query_one("#wizard-frame") is not None

    asyncio.run(run_check())


def test_chat_model_wizard_renders_discovered_model_choices(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """The chat journey runs through the generic Groundskeeping wizard.

    Dynamic discovery arrives as `future_fields` from the shared mutation provider,
    so the model step's choices come from the injected inventory seam rather than a
    Groundworkers-specific wizard controller.
    """
    pytest.importorskip("groundskeeping")

    from groundskeeping.app import OperatorApp
    from textual.widgets import Select

    from groundworkers.tui.app import build_groundworkers_tui_spec

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[connections.main]
dialect = "sqlite"
database_name = ":memory:"

[databases.cdm_db]
kind = "cdm"
connection = "main"
schema_name = "main"
vocab_schema = "main"

[tools.groundworkers]
cdm_db = "cdm_db"
llm_model_name = "chat_model"

[providers.chat_provider]
provider = "ollama"
base_url = "http://localhost:11434/v1"

[models.chat_model]
provider = "chat_provider"
model = "chat-model"
structured_output = true
""",
        encoding="utf-8",
    )

    def fake_discovery(provider_kind, base_url, api_key):
        assert provider_kind == "ollama"
        assert base_url == "http://localhost:11434/v1"
        return ("chat-model", "other-model")

    monkeypatch.setattr(
        "groundworkers.tui.pages.setup.discover_provider_models",
        fake_discovery,
    )

    async def run_check() -> None:
        app = OperatorApp(build_groundworkers_tui_spec(config_path=str(config_path)))

        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            sections = app.query_one("#sections")
            sections.focus()
            sections.highlighted = 2
            await pilot.press("enter")
            await pilot.pause()
            await pilot.click("#view-action-0")
            await pilot.pause()
            await pilot.click("#wizard-next")
            await pilot.pause()

            choices = app.screen.query_one("#wizard-field-1", Select)
            assert choices.value == "chat-model"
            assert [value for _label, value in choices._options] == [
                "chat-model",
                "other-model",
            ]

    asyncio.run(run_check())


def test_llm_provider_detail_renders_model_inventory_table(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pytest.importorskip("groundskeeping")

    from groundskeeping.app import OperatorApp

    from groundworkers.application.setup.models import (
        LlmModelMetadata,
        LlmProviderCheckResult,
    )
    from groundworkers.tui.app import build_groundworkers_tui_spec

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[connections.main]
dialect = "sqlite"
database_name = ":memory:"

[databases.cdm_db]
kind = "cdm"
connection = "main"
schema_name = "main"
vocab_schema = "main"

[tools.groundworkers]
cdm_db = "cdm_db"
llm_model_name = "chat_model"

[providers.chat_provider]
provider = "ollama"
base_url = "http://localhost:11434/v1"

[models.chat_model]
provider = "chat_provider"
model = "chat-model"
structured_output = true
""",
        encoding="utf-8",
    )

    def fake_verify(_snapshot):
        return LlmProviderCheckResult(
            provider="ollama",
            api_base="http://localhost:11434/v1",
            default_model_name="chat-model",
            reachable=True,
            model_available=True,
            inventory=("chat-model", "other-model"),
            model_metadata=(
                LlmModelMetadata(
                    name="chat-model",
                    size_bytes=4_294_967_296,
                    parameter_size="7B",
                    quantization_level="Q4_K_M",
                ),
            ),
        )

    monkeypatch.setattr(
        "groundworkers.tui.pages.setup.verify_llm_provider", fake_verify
    )

    async def run_check() -> None:
        app = OperatorApp(build_groundworkers_tui_spec(config_path=str(config_path)))

        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            sections = app.query_one("#sections")
            sections.focus()
            sections.highlighted = 2
            await pilot.press("enter")
            await pilot.pause()
            await pilot.click("#view-action-1")
            await pilot.pause(0.2)

            detail = app.query_one("#context-table")
            assert detail.ordered_columns[0].label.plain == "Model"
            assert detail.ordered_columns[1].label.plain == "Size"
            assert detail.get_row_at(0)[0] == "chat-model"
            assert detail.get_row_at(0)[1] == "4.0 GB"
            assert detail.get_row_at(0)[2] == "7B"
            assert detail.get_row_at(1)[0] == "other-model"

    asyncio.run(run_check())


def test_embedding_coverage_refresh_shows_loading_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pytest.importorskip("groundskeeping")

    from time import sleep

    from groundskeeping.app import OperatorApp

    from groundworkers.tui.app import build_groundworkers_tui_spec

    config_path = tmp_path / "config.toml"
    _write_embedding_stack(config_path)

    def slow_refresh(*_args, **_kwargs):
        sleep(0.4)

    monkeypatch.setattr(
        "groundworkers.tui.pages.setup.load_embedding_coverage_report",
        slow_refresh,
    )

    async def run_check() -> None:
        app = OperatorApp(build_groundworkers_tui_spec(config_path=str(config_path)))

        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            sections = app.query_one("#sections")
            sections.focus()
            sections.highlighted = 3
            await pilot.press("enter")
            await pilot.pause()

            assert str(app.query_one("#view-action-0").label) == "Check model"
            await pilot.click("#view-action-1")
            await pilot.pause(0.05)

            assert app.query_one("#result-loading").styles.display == "block"
            assert app.query_one("#result-table").styles.display == "none"
            assert "Counting CDM vocabularies" in str(
                app.query_one("#result-summary").render()
            )
            assert app.query_one("#context-panel").border_title == "Vocabulary coverage"

    asyncio.run(run_check())


def test_embedding_coverage_refresh_places_vocabularies_in_detail_pane(
    tmp_path: Path,
    monkeypatch,
) -> None:
    pytest.importorskip("groundskeeping")

    from groundskeeping.app import OperatorApp

    from groundworkers.application.setup.models import (
        CoverageScope,
        CoverageSnapshot,
        EmbeddingConfiguration,
        EmbeddingCoverageReport,
        EmbeddingIndexSnapshot,
        VocabularyCoverage,
    )
    from groundworkers.tui.app import build_groundworkers_tui_spec

    config_path = tmp_path / "config.toml"
    _write_embedding_stack(config_path)

    report = EmbeddingCoverageReport(
        configuration=EmbeddingConfiguration(
            backend="sqlitevec",
            vector_store_name="embedding_store",
            database_name="embedding_db",
            connection_name="embedding_main",
            database_safe_url="sqlite:///embeddings.db",
            provider_name="embedding_provider",
            provider_kind="ollama",
            model_entry_name="embedding_model",
            model_name="test-model",
            embeddings_supported=True,
            api_base="http://localhost:11434/v1",
        ),
        coverage=CoverageSnapshot(
            scope=CoverageScope(
                model_name="test-model",
                metric="cosine",
                vocabularies=("SNOMED", "LOINC"),
                standard_only=True,
                valid_only=False,
            ),
            available=True,
            rows=(
                VocabularyCoverage("SNOMED", 10, 4, 6, 40.0),
                VocabularyCoverage("LOINC", 5, 5, 0, 100.0),
            ),
            eligible_total=15,
            embedded_total=9,
            pending_total=6,
        ),
        index=EmbeddingIndexSnapshot(
            model_name="test-model",
            registered=True,
            storage_identifier="emb_test",
            registry_index_type="flat",
        ),
    )

    monkeypatch.setattr(
        "groundworkers.tui.pages.setup.load_embedding_coverage_report",
        lambda *_args, **_kwargs: report,
    )

    async def run_check() -> None:
        app = OperatorApp(build_groundworkers_tui_spec(config_path=str(config_path)))

        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            sections = app.query_one("#sections")
            sections.focus()
            sections.highlighted = 3
            await pilot.press("enter")
            await pilot.pause()
            await pilot.click("#view-action-1")
            await pilot.pause(0.2)

            setup = app.query_one("#result-table")
            assert setup.get_row_at(3)[0] == "Index"
            assert setup.get_row_at(3)[1] == "FLAT / exact scan"

            detail = app.query_one("#context-table")
            assert app.query_one("#context-panel").border_title == "Vocabulary coverage"
            assert detail.ordered_columns[0].label.plain == "Vocabulary"
            assert detail.get_row_at(0)[0] == "All vocabularies"
            assert detail.get_row_at(0)[3] == "6"
            assert detail.get_row_at(1)[0] == "SNOMED"
            assert detail.get_row_at(2)[0] == "LOINC"

    asyncio.run(run_check())


def test_a_wizard_text_box_is_actually_drawn(tmp_path: Path) -> None:
    """A zero-height field is focusable, editable, and invisible.

    Groundskeeping's theme sizes every ``TextArea`` at ``height: 1fr``. Inside a
    wizard the labels, help lines, and sibling fields take their auto height
    first, and the remaining fraction rounds to nothing -- so the widget accepts
    typing it can never show, and reads as a box that ignores the keyboard.
    Asserting on the rendered height is the only thing that catches this; every
    contract-level check passes on a widget nobody can see.
    """
    pytest.importorskip("groundskeeping")

    from textual.widgets import TextArea

    from groundworkers.application.setup.models import (
        CoverageScope,
        CoverageSnapshot,
        EmbeddingCoverageReport,
        EmbeddingIndexSnapshot,
        VocabularyCoverage,
    )
    from groundworkers.tui.app import (
        build_groundworkers_app,
        build_groundworkers_tui_spec,
    )
    from groundworkers.tui.state import SetupSession
    from groundworkers.tui.wizards.embeddings import (
        EmbeddingPopulationWizardController,
    )

    config_path = tmp_path / "config.toml"
    save_stack_config(build_embedding_stack(), config_path)

    rows = (VocabularyCoverage("SNOMED", 100, 40, 60, 40.0),)
    report = EmbeddingCoverageReport(
        configuration=_embedding_configuration(str(config_path)),
        coverage=CoverageSnapshot(
            scope=CoverageScope(
                model_name="arctic:v1",
                metric="cosine",
                vocabularies=("SNOMED",),
                standard_only=True,
                valid_only=False,
            ),
            available=True,
            rows=rows,
            eligible_total=100,
            embedded_total=40,
            pending_total=60,
        ),
        index=EmbeddingIndexSnapshot(model_name="arctic:v1", registered=True),
    )

    async def run_check() -> None:
        app_class = build_groundworkers_app()
        app = app_class(build_groundworkers_tui_spec(config_path=str(config_path)))

        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_wizard(
                EmbeddingPopulationWizardController(
                    SetupSession(), coverage=report, launcher=lambda command: None
                )
            )
            await pilot.pause()

            box = app.screen.query_one(TextArea)
            assert box.read_only is False
            assert box.disabled is False
            assert box.size.height > 1

            box.focus()
            await pilot.pause()
            await pilot.press("A")
            await pilot.pause()
            assert box.text.startswith("A")

    asyncio.run(run_check())
