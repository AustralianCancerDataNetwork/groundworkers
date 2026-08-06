from __future__ import annotations

from groundskeeping.contracts import (
    NavigationItem,
    PageContext,
    PageRoute,
    SectionItem,
    SectionNavigation,
    SurfaceView,
    TableView,
    TextView,
)

from groundworkers.application.setup.databases import (
    resolve_database_targets,
    verify_database_target,
)
from groundworkers.application.setup.embedding_setup import load_embedding_configuration
from groundworkers.application.setup.embedding_population import (
    load_embedding_coverage_report,
)
from groundworkers.application.setup.runtime_setup import (
    load_chat_configuration,
    load_graph_configuration,
    load_llm_provider_configuration,
    verify_llm_provider,
)
from groundworkers.tui.pages.base import GroundworkersPage
from groundworkers.tui.presenters.chat import ChatPresenter
from groundworkers.tui.presenters.base import key_value_detail
from groundworkers.tui.presenters.database import DatabasePresenter
from groundworkers.tui.presenters.embeddings import EmbeddingsPresenter
from groundworkers.tui.presenters.graph import GraphPresenter
from groundworkers.tui.presenters.llm_provider import LlmProviderPresenter
from groundworkers.tui.state import SetupSession
from groundworkers.tui.wizards.database import DatabaseConfigurationWizardController
from groundworkers.tui.wizards.embeddings import EmbeddingPopulationWizardController
from groundworkers.tui.wizards.llm_provider import (
    LlmProviderConfigurationWizardController,
)

DATABASE_SECTION = "setup.database"
GRAPH_SECTION = "setup.graph"
LLM_PROVIDER_SECTION = "setup.llm_provider"
EMBEDDINGS_SECTION = "setup.embeddings"
CHAT_SECTION = "setup.chat"

SECTION_TITLES = {
    DATABASE_SECTION: "Database Setup",
    GRAPH_SECTION: "Graph Setup",
    LLM_PROVIDER_SECTION: "LLM Provider Setup",
    EMBEDDINGS_SECTION: "Embeddings Setup",
    CHAT_SECTION: "Chat Setup",
}


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
        self._selected_database_target_key: str | None = None

    def activate(self, context: PageContext) -> None:
        self._show_section_detail(context)

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
                    status=self._llm_provider.status(
                        llm_provider,
                        self._session.llm_provider_result,
                    ),
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
                load_llm_provider_configuration(self._session.configuration),
                self._session.llm_provider_result,
            )
        if self._selected_section == EMBEDDINGS_SECTION:
            return self._embeddings.landing(
                database_ready=database_ready,
                configuration=load_embedding_configuration(self._session.configuration),
                coverage=self._session.embedding_coverage,
                selected_all=self._session.embedding_vocabulary_selection_all,
                selected_vocabularies=self._session.embedding_selected_vocabularies,
            )
        if self._selected_section == CHAT_SECTION:
            provider = load_llm_provider_configuration(self._session.configuration)
            return self._chat.landing(load_chat_configuration(provider))
        return self._database.landing(
            self._session.configuration,
            resolve_database_targets(self._session.configuration),
            self._session.connection_results,
            selected_target_key=self._selected_database_target_key,
        )

    def navigation_selected(self, item: NavigationItem, context: PageContext) -> None:
        if isinstance(item, SectionItem):
            self._selected_section = item.key
            if item.key != DATABASE_SECTION:
                self._selected_database_target_key = None
        self._show_current_view(context)

    def _show_current_view(self, context: PageContext) -> None:
        context.surface.show_view(self.route.key, self.landing_view(context))
        self._show_section_detail(context)

    def row_highlighted(self, row_key: str, context: PageContext) -> None:
        if self._selected_section != DATABASE_SECTION:
            return
        previous_key = self._selected_database_target_key
        self._selected_database_target_key = row_key
        view = self.landing_view(context)
        if not isinstance(view, TableView):
            return
        if previous_key != row_key:
            self._show_current_database_actions(view)
        row = next((item for item in view.rows if item.key == row_key), None)
        if row is None or row.detail is None:
            return
        detail = key_value_detail("Database detail", row.detail)
        if detail is not None:
            context.surface.show_detail(self.route.key, detail)

    def row_selected(self, row_key: str, context: PageContext) -> None:
        self.row_highlighted(row_key, context)

    def selection_changed(
        self,
        row_key: str,
        selected_keys: tuple[str, ...],
        context: PageContext,
    ) -> None:
        if self._selected_section != EMBEDDINGS_SECTION:
            return
        self._set_embedding_vocabulary_selection(row_key, selected_keys, context)

    def action_selected(self, action_key: str, context: PageContext) -> None:
        if action_key == "database.configure":
            if self._selected_database_target_key == "database.groundworkers":
                context.notify(
                    "Groundworkers tuning is derived from CDM and embedding resources; configure those resources directly.",
                    severity="warning",
                )
                return
            context.open_wizard(DatabaseConfigurationWizardController(self._session))
            return
        if action_key == "database.refresh":
            self._session.refresh_configuration()
            self._show_current_state(context)
            return
        if action_key == "database.test_connections":
            self._start_connection_checks(context)
            return
        if action_key == "llm_provider.test":
            self._start_llm_provider_check(context)
            return
        if action_key == "llm_provider.configure":
            context.open_wizard(LlmProviderConfigurationWizardController(self._session))
            return
        if action_key == "embeddings.refresh_coverage":
            self._start_embedding_coverage_refresh(context)
            return
        if action_key == "embeddings.populate":
            if self._session.embedding_coverage is None:
                self._start_embedding_coverage_refresh(context)
                context.notify("Refreshing embedding coverage before population.")
                return
            if (
                not self._session.embedding_vocabulary_selection_all
                and not self._session.embedding_selected_vocabularies
            ):
                context.notify(
                    "Select all missing vocabularies or at least one incomplete vocabulary before populating.",
                    severity="warning",
                )
                return
            context.open_wizard(
                EmbeddingPopulationWizardController(
                    self._session,
                    coverage=self._session.embedding_coverage,
                    vocabulary_mode=(
                        "all"
                        if self._session.embedding_vocabulary_selection_all
                        else "selected"
                    ),
                    vocabularies=self._session.embedding_selected_vocabularies,
                )
            )
            return

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

    def _start_llm_provider_check(self, context: PageContext) -> None:
        context.notify("Checking LLM provider endpoint and model.")

        def verify() -> None:
            result = verify_llm_provider(self._session.configuration)
            self.app.call_from_thread(
                self._finish_llm_provider_check,
                result,
                context,
            )

        self.run_worker(verify, thread=True, exclusive=True)

    def _finish_llm_provider_check(self, result, context: PageContext) -> None:
        self._session.llm_provider_result = result
        self._show_current_state(context)

    def _start_embedding_coverage_refresh(self, context: PageContext) -> None:
        context.notify("Refreshing embedding coverage.")
        context.surface.show_view(self.route.key, self._embeddings.loading())
        context.surface.show_detail(
            self.route.key,
            TextView(
                title="Vocabulary coverage",
                body="Waiting for CDM vocabulary counts and vector-store counts.",
            ),
        )
        self.app.refresh(layout=True)

        def refresh() -> None:
            result = load_embedding_coverage_report(
                self._session.configuration,
                standard_only=self._session.embedding_standard_only,
            )
            self.app.call_from_thread(
                self._finish_embedding_coverage_refresh,
                result,
                context,
            )

        self.run_worker(refresh, thread=True, exclusive=True)

    def _finish_embedding_coverage_refresh(self, result, context: PageContext) -> None:
        self._session.embedding_coverage = result
        self._sync_embedding_vocabulary_selection()
        self._show_current_state(context)

    def _show_current_state(self, context: PageContext) -> None:
        context.surface.show_navigation(
            self.route.key,
            self.build_navigation(context),
        )
        self._show_current_view(context)

    def _show_current_database_actions(self, view: TableView) -> None:
        workbench = self.app.query_one("#workbench")
        show_actions = getattr(workbench, "show_actions", None)
        if callable(show_actions):
            show_actions(view.actions)

    def _show_section_detail(self, context: PageContext) -> None:
        if self._selected_section == LLM_PROVIDER_SECTION:
            context.surface.show_detail(
                self.route.key,
                self._llm_provider.detail(
                    load_llm_provider_configuration(self._session.configuration),
                    self._session.llm_provider_result,
                ),
            )
            return
        if self._selected_section == EMBEDDINGS_SECTION:
            context.surface.show_detail(
                self.route.key,
                self._embeddings.detail(
                    load_embedding_configuration(self._session.configuration),
                    self._session.embedding_coverage,
                    selected_all=self._session.embedding_vocabulary_selection_all,
                    selected_vocabularies=self._session.embedding_selected_vocabularies,
                ),
            )
            return
        context.surface.show_detail(
            self.route.key,
            TextView(title=self._selected_section_title(), body=""),
        )

    def _selected_section_title(self) -> str:
        return SECTION_TITLES.get(self._selected_section, "Setup")

    def _set_embedding_vocabulary_selection(
        self,
        row_key: str,
        selected_keys: tuple[str, ...],
        context: PageContext,
    ) -> None:
        coverage = self._session.embedding_coverage
        if coverage is None or not coverage.coverage.available:
            return
        all_key = "embeddings.coverage.all"
        if row_key == all_key or all_key in selected_keys or not selected_keys:
            self._session.embedding_vocabulary_selection_all = True
            self._session.embedding_selected_vocabularies = ()
            self._show_current_view(context)
            return
        prefix = "embeddings.coverage."
        if not row_key.startswith(prefix):
            return
        selected_keys_set = set(selected_keys)
        pending_by_vocabulary = {
            row.vocabulary: row.pending for row in coverage.coverage.rows
        }
        selected = {
            key.removeprefix(prefix)
            for key in selected_keys_set
            if key.startswith(prefix)
        }
        selected = {
            vocabulary
            for vocabulary in selected
            if pending_by_vocabulary.get(vocabulary, 0) > 0
        }
        self._session.embedding_vocabulary_selection_all = False
        self._session.embedding_selected_vocabularies = tuple(
            row.vocabulary
            for row in coverage.coverage.rows
            if row.vocabulary in selected
        )
        self._show_current_view(context)

    def _sync_embedding_vocabulary_selection(self) -> None:
        coverage = self._session.embedding_coverage
        if coverage is None or not coverage.coverage.available:
            self._session.embedding_vocabulary_selection_all = True
            self._session.embedding_selected_vocabularies = ()
            return
        incomplete = set(coverage.incomplete_vocabularies)
        selected = tuple(
            vocabulary
            for vocabulary in self._session.embedding_selected_vocabularies
            if vocabulary in incomplete
        )
        if selected:
            self._session.embedding_vocabulary_selection_all = False
            self._session.embedding_selected_vocabularies = selected
            return
        self._session.embedding_vocabulary_selection_all = True
        self._session.embedding_selected_vocabularies = ()
