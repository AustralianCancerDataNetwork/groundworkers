from __future__ import annotations

from groundskeeping.contracts import (
    NavigationItem,
    PageContext,
    PageRoute,
    SectionItem,
    SectionNavigation,
    SurfaceView,
)

from groundworkers.application.setup.databases import (
    resolve_database_targets,
    verify_database_target,
)
from groundworkers.application.setup.embedding_setup import load_embedding_configuration
from groundworkers.application.setup.runtime_setup import (
    load_chat_configuration,
    load_graph_configuration,
    load_llm_provider_configuration,
)
from groundworkers.tui.pages.base import GroundworkersPage
from groundworkers.tui.presenters.chat import ChatPresenter
from groundworkers.tui.presenters.database import DatabasePresenter
from groundworkers.tui.presenters.embeddings import EmbeddingsPresenter
from groundworkers.tui.presenters.graph import GraphPresenter
from groundworkers.tui.presenters.llm_provider import LlmProviderPresenter
from groundworkers.tui.state import SetupSession

DATABASE_SECTION = "setup.database"
GRAPH_SECTION = "setup.graph"
LLM_PROVIDER_SECTION = "setup.llm_provider"
EMBEDDINGS_SECTION = "setup.embeddings"
CHAT_SECTION = "setup.chat"


class SetupPage(GroundworkersPage):
    """Coordinate setup sections while leaving their presentation modular."""

    def __init__(
        self,
        route: PageRoute,
        session: SetupSession,
        *,
        database: DatabasePresenter,
        graph: GraphPresenter,
        llm_provider: LlmProviderPresenter,
        embeddings: EmbeddingsPresenter,
        chat: ChatPresenter,
    ) -> None:
        super().__init__(route)
        self._session = session
        self._database = database
        self._graph = graph
        self._llm_provider = llm_provider
        self._embeddings = embeddings
        self._chat = chat
        self._selected_section = DATABASE_SECTION

    def build_navigation(self, context: PageContext) -> SectionNavigation:
        graph = load_graph_configuration(self._session.configuration)
        llm_provider = load_llm_provider_configuration(self._session.configuration)
        embeddings = load_embedding_configuration(self._session.configuration)
        chat = load_chat_configuration(llm_provider)
        database_ready = self._session.databases_connected
        return SectionNavigation(
            title="Setup",
            items=(
                SectionItem(
                    DATABASE_SECTION,
                    "Database",
                    status=self._database.status(
                        self._session.configuration,
                        self._session.connection_results,
                    ),
                ),
                SectionItem(
                    GRAPH_SECTION,
                    "Graph",
                    status=self._graph.status(
                        database_ready=database_ready,
                        configuration=graph,
                    ),
                ),
                SectionItem(
                    LLM_PROVIDER_SECTION,
                    "LLM Provider",
                    status=self._llm_provider.status(llm_provider),
                ),
                SectionItem(
                    EMBEDDINGS_SECTION,
                    "Embeddings",
                    status=self._embeddings.status(
                        database_ready=database_ready,
                        configured=embeddings is not None,
                    ),
                ),
                SectionItem(
                    CHAT_SECTION,
                    "Chat",
                    status=self._chat.status(chat),
                ),
            ),
        )

    def landing_view(self, context: PageContext) -> SurfaceView:
        database_ready = self._session.databases_connected
        if self._selected_section == GRAPH_SECTION:
            return self._graph.landing(
                database_ready=database_ready,
                configuration=load_graph_configuration(self._session.configuration),
            )
        if self._selected_section == LLM_PROVIDER_SECTION:
            return self._llm_provider.landing(
                load_llm_provider_configuration(self._session.configuration)
            )
        if self._selected_section == EMBEDDINGS_SECTION:
            return self._embeddings.landing(
                database_ready=database_ready,
                configuration=load_embedding_configuration(self._session.configuration),
            )
        if self._selected_section == CHAT_SECTION:
            provider = load_llm_provider_configuration(self._session.configuration)
            return self._chat.landing(load_chat_configuration(provider))
        return self._database.landing(
            self._session.configuration,
            resolve_database_targets(self._session.configuration),
            self._session.connection_results,
        )

    def navigation_selected(self, item: NavigationItem, context: PageContext) -> None:
        if isinstance(item, SectionItem):
            self._selected_section = item.key
        context.surface.show_view(self.route.key, self.landing_view(context))

    def action_selected(self, action_key: str, context: PageContext) -> None:
        if action_key == "database.refresh":
            self._session.refresh_configuration()
            self._show_current_state(context)
            return
        if action_key == "database.test_connections":
            self._start_connection_checks(context)

    def _start_connection_checks(self, context: PageContext) -> None:
        targets = resolve_database_targets(self._session.configuration)
        if not targets:
            context.notify(
                "Resolve the configuration issues before testing connections.",
                severity="warning",
            )
            return
        context.surface.show_view(self.route.key, self._database.loading())

        def verify() -> None:
            results = tuple(verify_database_target(target) for target in targets)
            self.app.call_from_thread(
                self._finish_connection_checks,
                results,
                context,
            )

        self.run_worker(verify, thread=True, exclusive=True)

    def _finish_connection_checks(self, results, context: PageContext) -> None:
        self._session.connection_results = tuple(results)
        self._show_current_state(context)

    def _show_current_state(self, context: PageContext) -> None:
        context.surface.show_navigation(
            self.route.key,
            self.build_navigation(context),
        )
        context.surface.show_view(self.route.key, self.landing_view(context))
