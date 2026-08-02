from __future__ import annotations

import asyncio
from pathlib import Path

import pytest


def test_groundworkers_spec_keeps_setup_as_a_registered_page() -> None:
    pytest.importorskip("groundskeeping")

    from groundworkers.tui.app import build_groundworkers_tui_spec

    spec = build_groundworkers_tui_spec(
        config_path="/definitely/not/a/groundworkers-config.toml",
        profile="test",
    )

    assert spec.validate().keys() == ("setup",)
    assert spec.default_page == "setup"
    assert spec.title == "Groundworkers"


def test_groundworkers_pages_do_not_cover_workbench() -> None:
    pytest.importorskip("groundskeeping")

    from groundskeeping.app import OperatorApp

    from groundworkers.tui.app import build_groundworkers_tui_spec

    async def run_check() -> None:
        app = OperatorApp(
            build_groundworkers_tui_spec(
                config_path="/definitely/not/a/groundworkers-config.toml",
                profile="test",
            )
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
            assert sections.option_count == 5
            assert tuple(
                sections.get_option_at_index(index).id
                for index in range(sections.option_count)
            ) == (
                "setup.database",
                "setup.graph",
                "setup.llm_provider",
                "setup.embeddings",
                "setup.chat",
            )

            sections.focus()
            sections.highlighted = 2
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            assert "LLM provider not configured" in str(
                app.query_one("#result-summary").render()
            )

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
[databases.main]
dialect = "sqlite"
database_name = ":memory:"

[resources.cdm_db]
database = "main"
cdm_schema = "main"
vocab_schema = "main"
""",
        encoding="utf-8",
    )

    async def run_check() -> None:
        app = OperatorApp(build_groundworkers_tui_spec(config_path=str(config_path)))

        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause()
            assert app.query_one("#catalogue").styles.display == "none"
            assert str(app.query_one("#view-action-0").label) == "Test connections"
            assert str(app.query_one("#view-action-1").label) == "Refresh"

            await pilot.click("#view-action-0")
            await pilot.pause(0.2)

            table = app.query_one("#result-table")
            assert table.get_row_at(0)[3] == "Connected"

    asyncio.run(run_check())
