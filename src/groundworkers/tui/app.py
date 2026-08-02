from __future__ import annotations

from typing import Any

from groundworkers.tui.state import SetupSession, load_tui_state


def build_groundworkers_tui_spec(
    *,
    config_path: str | None = None,
    profile: str | None = None,
):
    """Build the Groundworkers console with setup as its first registered page."""

    from groundskeeping.app import OperatorAppSpec
    from groundskeeping.contracts import PageRegistration

    from groundworkers.tui.pages import SetupPage
    from groundworkers.tui.presenters.chat import ChatPresenter
    from groundworkers.tui.presenters.database import DatabasePresenter
    from groundworkers.tui.presenters.embeddings import EmbeddingsPresenter
    from groundworkers.tui.presenters.graph import GraphPresenter
    from groundworkers.tui.presenters.llm_provider import LlmProviderPresenter
    from groundworkers.tui.routes import SETUP_ROUTE

    session = load_tui_state(config_path=config_path, profile=profile)

    def setup_factory(_context: Any):
        return SetupPage(
            SETUP_ROUTE,
            session,
            database=DatabasePresenter(),
            graph=GraphPresenter(),
            llm_provider=LlmProviderPresenter(),
            embeddings=EmbeddingsPresenter(),
            chat=ChatPresenter(),
        )

    return OperatorAppSpec(
        app_id="groundworkers",
        title="Groundworkers",
        subtitle="setup and operations",
        default_page=SETUP_ROUTE.key,
        pages=(PageRegistration(route=SETUP_ROUTE, factory=setup_factory),),
        metadata={
            "config_path": str(session.configuration.path),
            "profile": session.configuration.profile,
            "config_state": session.configuration.state.value,
        },
    )


def run_groundworkers_tui(
    *,
    config_path: str | None = None,
    profile: str | None = None,
) -> None:
    from groundskeeping.app import OperatorApp

    OperatorApp(
        build_groundworkers_tui_spec(config_path=config_path, profile=profile)
    ).run()


__all__ = ["SetupSession", "build_groundworkers_tui_spec", "run_groundworkers_tui"]
