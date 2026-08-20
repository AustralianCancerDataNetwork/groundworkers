from __future__ import annotations

from collections.abc import Callable

from groundskeeping.configurator import (
    ConfigTarget,
    ConfigWizardController,
    ConfigWorkflowSpec,
    MutationOperation,
    resolve_operation,
)
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

from groundworkers._env import rejected_config_path
from groundworkers.application.setup.configuration_provider import (
    CDM_SETUP_TARGET,
    LLM_SETUP_TARGET,
    MODEL_SETUP_TARGET,
    VECTOR_STORE_SETUP_TARGET,
    GroundworkersConfigMutationService,
    cdm_setup_workflow,
    llm_setup_workflow,
    model_setup_workflow,
    vector_store_setup_workflow,
)
from groundworkers.application.setup.databases import (
    resolve_database_targets,
    verify_database_target,
)
from groundworkers.application.setup.model_inventory import discover_provider_models
from groundworkers.application.setup.models import ConfigurationState
from groundworkers.application.setup.runtime_setup import (
    load_chat_configuration,
    load_graph_configuration,
    load_llm_provider_configuration,
    verify_llm_provider,
)
from groundworkers.tui.pages.base import GroundworkersPage
from groundworkers.tui.presenters.base import key_value_detail
from groundworkers.tui.presenters.chat import ChatPresenter
from groundworkers.tui.presenters.configuration import ConfigurationPresenter
from groundworkers.tui.presenters.database import (
    EMBEDDING_TARGET_KEY,
    DatabasePresenter,
)
from groundworkers.tui.presenters.embeddings import EmbeddingsPresenter
from groundworkers.tui.presenters.graph import GraphPresenter
from groundworkers.tui.presenters.llm_provider import LlmProviderPresenter
from groundworkers.tui.state import SetupSession
from groundworkers.tui.wizards.config_location import ConfigLocationWizardController
from groundworkers.tui.wizards.graph_maintenance import GraphMaintenanceWizardController

DATABASE_SECTION = "setup.database"
GRAPH_SECTION = "setup.graph"
LLM_PROVIDER_SECTION = "setup.llm_provider"
EMBEDDINGS_SECTION = "setup.embeddings"
CHAT_SECTION = "setup.chat"
CONFIGURATION_SECTION = "setup.configuration"

# The database target whose diagnostics decide the Graph section's status.
GRAPH_READINESS_TARGET = "database.graph"

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
        configuration: ConfigurationPresenter,
    ) -> None:
        super().__init__(route)
        self._session = session
        self._database = database
        self._graph = graph
        self._llm_provider = llm_provider
        self._embeddings = embeddings
        self._chat = chat
        self._configuration = configuration
        self._config_location_offered = False
        self._selected_section = DATABASE_SECTION
        self._selected_database_target_key: str | None = None

    def activate(self, context: PageContext) -> None:
        if self._should_choose_config_location():
            # Once per mount: with no configuration on disk there is nothing to
            # show and nowhere to write, so settle the path first. Every actual
            # configuration journey is unchanged and picks up from there.
            self._config_location_offered = True
            context.open_wizard(ConfigLocationWizardController(self._session))
            return
        self._show_section_detail(context)

    def _config_path_was_rejected(self) -> bool:
        """Whether OA_CONFIG_PATH named a file Groundworkers could not use.

        It is dropped before oa-configurator can refuse to start on it, which
        leaves the console reading the default instead. That is the right thing
        to keep running on and the wrong thing to do silently: the operator asked
        for somewhere else.
        """
        return rejected_config_path() is not None

    def _graph_readiness(self):
        """The connection check that already probes everything the graph needs."""
        return next(
            (
                result
                for result in self._session.connection_results
                if result.target_key == GRAPH_READINESS_TARGET
            ),
            None,
        )

    def _should_choose_config_location(self) -> bool:
        if self._config_location_offered:
            return False
        return (
            self._session.configuration.state is ConfigurationState.MISSING
            or self._config_path_was_rejected()
        )

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
                        readiness=self._graph_readiness(),
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
                        reconciliation=self._session.embedding_reconciliation,
                    ),
                ),
                SectionItem(
                    CHAT_SECTION,
                    "Chat",
                    status=self._chat.status(chat),
                ),
                SectionItem(
                    CONFIGURATION_SECTION,
                    "View Configuration",
                    status=self._configuration.status(self._session.configuration),
                ),
            ),
        )

    def landing_view(self, context: PageContext) -> SurfaceView:
        database_ready = self._session.databases_connected
        if self._selected_section == GRAPH_SECTION:
            return self._graph.landing(
                database_ready=database_ready,
                configuration=load_graph_configuration(self._session.configuration),
                readiness=self._graph_readiness(),
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
                reconciliation=self._session.embedding_reconciliation,
                selected_all=self._session.embedding_vocabulary_selection_all,
                selected_vocabularies=self._session.embedding_selected_vocabularies,
            )
        if self._selected_section == CONFIGURATION_SECTION:
            return self._configuration.landing(self._session.configuration)
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
        if action_key == "graph.prepare":
            context.open_wizard(
                GraphMaintenanceWizardController(
                    self._session, self._graph_readiness()
                )
            )
            return
        if action_key == "database.configure":
            if self._selected_database_target_key == EMBEDDING_TARGET_KEY:
                self._open_config_wizard(
                    context,
                    VECTOR_STORE_SETUP_TARGET,
                    vector_store_setup_workflow,
                )
                return
            if self._selected_database_target_key == "database.groundworkers":
                context.notify(
                    "Groundworkers tuning is derived from the CDM database, embedding model, and vector store; configure those entries directly.",
                    severity="warning",
                )
                return
            self._open_config_wizard(context, CDM_SETUP_TARGET, cdm_setup_workflow)
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
            self._open_config_wizard(context, LLM_SETUP_TARGET, llm_setup_workflow)
            return
        if action_key == "embeddings.configure_model":
            self._open_config_wizard(context, MODEL_SETUP_TARGET, model_setup_workflow)
            return
        if action_key == "embeddings.initialize_store":
            from groundworkers.tui.wizards.embedding_store import (
                EmbeddingStoreInitializationWizardController,
            )

            context.open_wizard(
                EmbeddingStoreInitializationWizardController(self._session)
            )
            return
        if action_key == "embeddings.check_model":
            self._start_embedding_model_check(context)
            return
        if action_key == "embeddings.refresh_coverage":
            self._start_embedding_coverage_refresh(context)
            return
        if action_key == "embeddings.populate":
            if self._session.embedding_coverage is None:
                self._start_embedding_coverage_refresh(context)
                context.notify("Refreshing embedding coverage before population.")
                return
            from groundworkers.tui.wizards.embeddings import (
                EmbeddingPopulationWizardController,
            )

            # Scope is the wizard's first step, so nothing has to be chosen on
            # the page before it opens. The gate that used to stand here asked
            # for a table selection the detail view never offered.
            context.open_wizard(
                EmbeddingPopulationWizardController(
                    self._session,
                    coverage=self._session.embedding_coverage,
                )
            )
            return

    def _open_config_wizard(
        self,
        context: PageContext,
        target: ConfigTarget,
        workflow: Callable[[MutationOperation], ConfigWorkflowSpec],
    ) -> None:
        """Open a setup journey through the one generic write flow.

        Every supported Groundworkers write goal — CDM database, embedding model,
        chat model — goes through this path: the shared mutation provider decides
        create-versus-update from the current configuration, and Groundskeeping's
        reusable controller owns the wizard, preview, redaction, and apply
        lifecycle. There is no second writer.
        """
        service = GroundworkersConfigMutationService(
            self._session.configuration.path,
            ownership=self._session.ownership,
            model_discoverer=discover_provider_models,
            on_applied=self._session.refresh_configuration,
        )
        context.open_wizard(
            ConfigWizardController(
                workflow(resolve_operation(service, target)),
                service,
            )
        )

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

    def _start_embedding_model_check(self, context: PageContext) -> None:
        """Ask the store and the provider whether the configured model works.

        Separate from the coverage refresh, and cheaper: coverage counts a whole
        vocabulary, this reads one registry and encodes one probe string. It is
        also the check that decides whether populating can succeed at all, so it
        is offered first.
        """
        context.notify("Checking the embedding store and provider.")

        def verify() -> None:
            result = verify_embedding_model(self._session.configuration)
            self.app.call_from_thread(
                self._finish_embedding_model_check,
                result,
                context,
            )

        self.run_worker(verify, thread=True, exclusive=True)

    def _finish_embedding_model_check(self, result, context: PageContext) -> None:
        self._session.embedding_reconciliation = result
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


def load_embedding_configuration(snapshot):
    """Keep CDM setup available while the embedding runtime is being migrated."""

    try:
        from groundworkers.application.setup.embedding_setup import (
            load_embedding_configuration as load,
        )
    except ImportError:
        return None
    return load(snapshot)


def load_embedding_coverage_report(snapshot, *, standard_only: bool):
    from groundworkers.application.setup.embedding_population import (
        load_embedding_coverage_report as load,
    )

    return load(snapshot, standard_only=standard_only)


def verify_embedding_model(snapshot):
    """Imported the same lazily-guarded way as the coverage report above, so a
    stack without the embedding extras still reaches CDM setup."""

    try:
        from groundworkers.application.setup.embedding_reconciliation import (
            verify_embedding_model as verify,
        )
    except ImportError:
        return None
    return verify(snapshot)
