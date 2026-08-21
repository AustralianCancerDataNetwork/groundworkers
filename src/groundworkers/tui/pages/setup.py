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
from groundworkers.application.setup.embedding_capability import (
    embedding_capability_state,
)
from groundworkers.application.setup.integration import build_integration_output
from groundworkers.application.setup.maintenance_runs import (
    MaintenanceRunner,
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
from groundworkers.tui.presenters.overview import OverviewPresenter
from groundworkers.tui.presenters.performance import PerformancePresenter
from groundworkers.tui.presenters.runs import RunsPresenter, format_progress_tail
from groundworkers.tui.state import SetupSession
from groundworkers.tui.wizards.config_location import ConfigLocationWizardController
from groundworkers.tui.wizards.graph_maintenance import GraphMaintenanceWizardController

OVERVIEW_SECTION = "setup.overview"
DATABASE_SECTION = "setup.database"
GRAPH_SECTION = "setup.graph"
LLM_PROVIDER_SECTION = "setup.llm_provider"
EMBEDDINGS_SECTION = "setup.embeddings"
CHAT_SECTION = "setup.chat"
CONFIGURATION_SECTION = "setup.configuration"
RUNS_SECTION = "setup.runs"
PERFORMANCE_SECTION = "setup.performance"
RUNS_REFRESH_INTERVAL = 15.0

# The database target whose diagnostics decide the Graph section's status.
GRAPH_READINESS_TARGET = "database.graph"

SECTION_TITLES = {
    OVERVIEW_SECTION: "Overview",
    DATABASE_SECTION: "Database Setup",
    GRAPH_SECTION: "Graph Setup",
    LLM_PROVIDER_SECTION: "Chat Model Setup",
    EMBEDDINGS_SECTION: "Embeddings Setup",
    CHAT_SECTION: "Chat Setup",
    RUNS_SECTION: "Runs",
    PERFORMANCE_SECTION: "Performance",
}


class SetupPage(GroundworkersPage):
    """Coordinate setup sections while leaving their presentation modular."""

    def __init__(
        self,
        route: PageRoute,
        session: SetupSession,
        *,
        database: DatabasePresenter,
        overview: OverviewPresenter | None = None,
        performance: PerformancePresenter | None = None,
        graph: GraphPresenter,
        llm_provider: LlmProviderPresenter,
        embeddings: EmbeddingsPresenter,
        chat: ChatPresenter,
        configuration: ConfigurationPresenter,
        runs: RunsPresenter | None = None,
    ) -> None:
        super().__init__(route)
        self._session = session
        self._database = database
        self._overview = overview or OverviewPresenter()
        self._performance = performance or PerformancePresenter()
        self._graph = graph
        self._llm_provider = llm_provider
        self._embeddings = embeddings
        self._chat = chat
        self._configuration = configuration
        self._runs = runs or RunsPresenter()
        self._config_location_offered = False
        self._selected_section = OVERVIEW_SECTION
        self._selected_database_target_key: str | None = None
        self._selected_run_id: str | None = None
        self._runs_refresh_timer = None
        self._runs_refresh_context: PageContext | None = None
        self._runs_refresh_in_flight = False

    def activate(self, context: PageContext) -> None:
        self._runs_refresh_context = context
        # Direct presenter/page tests call activate() without mounting the
        # widget. Textual timers require a running message pump; the real app
        # activates pages after mount, when is_running is true.
        if self.is_running and self._runs_refresh_timer is None:
            self._runs_refresh_timer = self.set_interval(
                RUNS_REFRESH_INTERVAL,
                self._refresh_runs,
            )
        if self._should_choose_config_location():
            # The default path is safe to use. Ask for a location only when the
            # caller supplied or rejected a different path.
            self._config_location_offered = True
            if (
                self._session.configuration.state is ConfigurationState.MISSING
                and self._session.config_path is None
                and not self._config_path_was_rejected()
            ):
                context.notify(
                    f"No configuration found; it will be created at {self._session.configuration.path}."
                )
                self._open_config_wizard(context, CDM_SETUP_TARGET, cdm_setup_workflow)
            else:
                context.open_wizard(ConfigLocationWizardController(self._session))
            return
        self._show_section_detail(context)

    def deactivate(self, context: PageContext) -> None:
        if self._runs_refresh_timer is not None:
            self._runs_refresh_timer.stop()
            self._runs_refresh_timer = None
        self._runs_refresh_context = None

    def _refresh_runs(self) -> None:
        """Refresh the selected Runs view without overlapping refreshes."""
        context = self._runs_refresh_context
        if (
            context is None
            or self._selected_section != RUNS_SECTION
            or self._runs_refresh_in_flight
        ):
            return
        self._runs_refresh_in_flight = True
        try:
            self._refresh_runs_surface(context)
        finally:
            self._runs_refresh_in_flight = False

    def _refresh_runs_surface(self, context: PageContext) -> None:
        """Refresh run cells through Groundskeeping's stable table API."""
        view = self._runs.landing(selected_run_id=self._selected_run_id)
        if not isinstance(view, TableView):
            self._show_current_view(context)
            return
        context.surface.refresh_view(self.route.key, view)
        self._show_section_detail(context)

    def _config_path_was_rejected(self) -> bool:
        """Whether OA_CONFIG_PATH named a file that could not be used."""
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
                    OVERVIEW_SECTION,
                    "Overview",
                    status=self._overview.status(
                        self._session.configuration,
                        connections=self._session.connection_results,
                        embedding_configuration=embeddings,
                        embedding_coverage=self._session.embedding_coverage,
                        embedding_reconciliation=self._session.embedding_reconciliation,
                        llm_result=self._session.llm_provider_result,
                        graph_ready=self._graph_is_ready(),
                        integration_ready=self._integration_ready(),
                    ),
                ),
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
                    PERFORMANCE_SECTION,
                    "Performance",
                    status=self._performance.status(
                        connections=self._session.connection_results,
                        embedding_configuration=embeddings,
                        embedding_coverage=self._session.embedding_coverage,
                    ),
                ),
                SectionItem(
                    LLM_PROVIDER_SECTION,
                    "Chat Model",
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
                        configuration=embeddings,
                        coverage=self._session.embedding_coverage,
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
                SectionItem(
                    RUNS_SECTION,
                    "Runs",
                ),
            ),
        )

    def landing_view(self, context: PageContext) -> SurfaceView:
        database_ready = self._session.databases_connected
        if self._selected_section == OVERVIEW_SECTION:
            return self._overview.landing(
                self._session.configuration,
                connections=self._session.connection_results,
                embedding_configuration=load_embedding_configuration(
                    self._session.configuration
                ),
                embedding_coverage=self._session.embedding_coverage,
                embedding_reconciliation=self._session.embedding_reconciliation,
                llm_result=self._session.llm_provider_result,
                graph_ready=self._graph_is_ready(),
                integration_ready=self._integration_ready(),
            )
        if self._selected_section == GRAPH_SECTION:
            return self._graph.landing(
                database_ready=database_ready,
                configuration=load_graph_configuration(self._session.configuration),
                readiness=self._graph_readiness(),
            )
        if self._selected_section == PERFORMANCE_SECTION:
            return self._performance.landing(
                connections=self._session.connection_results,
                embedding_configuration=load_embedding_configuration(
                    self._session.configuration
                ),
                embedding_coverage=self._session.embedding_coverage,
                can_prepare=self._performance_can_prepare(),
            )
        if self._selected_section == LLM_PROVIDER_SECTION:
            return self._llm_provider.landing(
                load_llm_provider_configuration(self._session.configuration),
                self._session.llm_provider_result,
                load_chat_configuration(
                    load_llm_provider_configuration(self._session.configuration)
                ),
                editable=self._session.ownership.editable,
            )
        if self._selected_section == EMBEDDINGS_SECTION:
            return self._embeddings.landing(
                database_ready=database_ready,
                configuration=load_embedding_configuration(self._session.configuration),
                coverage=self._session.embedding_coverage,
                reconciliation=self._session.embedding_reconciliation,
                selected_all=self._session.embedding_vocabulary_selection_all,
                selected_vocabularies=self._session.embedding_selected_vocabularies,
                editable=self._session.ownership.editable,
            )
        if self._selected_section == CONFIGURATION_SECTION:
            return self._configuration.landing(self._session.configuration)
        if self._selected_section == CHAT_SECTION:
            provider = load_llm_provider_configuration(self._session.configuration)
            return self._chat.landing(load_chat_configuration(provider))
        if self._selected_section == RUNS_SECTION:
            return self._runs.landing(selected_run_id=self._selected_run_id)
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
        if self._selected_section == RUNS_SECTION:
            self._selected_run_id = row_key
            self._refresh_runs_surface(context)
            return
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
        if self._selected_section in {RUNS_SECTION, DATABASE_SECTION}:
            self.row_highlighted(row_key, context)
            return
        view = self.landing_view(context)
        if not isinstance(view, TableView):
            return
        row = next((item for item in view.rows if item.key == row_key), None)
        if row is None or row.detail is None:
            return
        detail = key_value_detail(f"{self._selected_section_title()} detail", row.detail)
        if detail is not None:
            context.surface.show_detail(self.route.key, detail)

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
        if action_key == "overview.verify_all":
            self._start_verify_all(context)
            return
        if action_key == "overview.integration":
            output = self._integration_output() if self._session.databases_connected else None
            if output is None:
                context.notify("Verify the CDM before showing integration commands.", severity="warning")
            else:
                context.notify(f"stdio: {output.stdio_command}\nHTTP: {output.http_command}")
            return
        if action_key == "graph.prepare":
            context.open_wizard(
                GraphMaintenanceWizardController(
                    self._session, self._graph_readiness()
                )
            )
            return
        if action_key == "performance.refresh":
            self._start_performance_checks(context)
            return
        if action_key == "performance.prepare":
            from groundworkers.tui.wizards.performance_maintenance import (
                PerformanceMaintenanceWizardController,
            )

            context.open_wizard(
                PerformanceMaintenanceWizardController(
                    self._session,
                    embedding_coverage=self._session.embedding_coverage,
                    trigram_available=self._trigram_available(),
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
        if action_key == "runs.refresh":
            self._show_current_state(context)
            return
        if action_key in {"runs.cancel", "runs.retry", "runs.postflight", "runs.export"}:
            self._handle_run_action(action_key, context)
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
        if action_key == "chat.configure":
            self._open_config_wizard(context, MODEL_SETUP_TARGET, model_setup_workflow)
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
            capability = embedding_capability_state(
                load_embedding_configuration(self._session.configuration),
                self._session.embedding_coverage,
                self._session.embedding_reconciliation,
            )
            if not capability.can_populate:
                context.notify(
                    capability.blockers[0]
                    if capability.blockers
                    else "Embedding population prerequisites are incomplete.",
                    severity="warning",
                )
                return
            from groundworkers.tui.wizards.embeddings import (
                EmbeddingPopulationWizardController,
            )

            # The wizard starts by choosing the population scope.
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
        """Open a configuration wizard backed by the shared write flow."""
        if not self._session.ownership.editable:
            context.notify(
                f"{self._session.ownership.source_label}: "
                f"{self._session.ownership.guidance}",
                severity="warning",
            )
            return
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

    def _start_performance_checks(self, context: PageContext) -> None:
        targets = resolve_database_targets(self._session.configuration)
        if not targets:
            context.notify(
                "Resolve the configuration issues before checking performance indexes.",
                severity="warning",
            )
            return
        context.notify("Checking performance indexes.")

        def verify() -> None:
            results = tuple(verify_database_target(target) for target in targets)
            coverage = load_embedding_coverage_report(
                self._session.configuration,
                standard_only=self._session.embedding_standard_only,
            )
            self.app.call_from_thread(
                self._finish_performance_checks,
                results,
                coverage,
                context,
            )

        self.run_worker(verify, thread=True, exclusive=True)

    def _finish_performance_checks(self, results, coverage, context: PageContext) -> None:
        self._session.connection_results = tuple(results)
        self._session.embedding_coverage = coverage
        self._show_current_state(context)

    def _start_verify_all(self, context: PageContext) -> None:
        """Run the required and configured capability checks as one action."""

        context.notify("Verifying configured capabilities.")

        def verify() -> None:
            targets = resolve_database_targets(self._session.configuration)
            connections = tuple(verify_database_target(target) for target in targets)
            llm = verify_llm_provider(self._session.configuration)
            coverage = load_embedding_coverage_report(
                self._session.configuration,
                standard_only=self._session.embedding_standard_only,
            )
            reconciliation = verify_embedding_model(self._session.configuration)
            self.app.call_from_thread(
                self._finish_verify_all,
                connections,
                llm,
                coverage,
                reconciliation,
                context,
            )

        self.run_worker(verify, thread=True, exclusive=True)

    def _finish_verify_all(
        self,
        connections,
        llm,
        coverage,
        reconciliation,
        context: PageContext,
    ) -> None:
        self._session.connection_results = tuple(connections)
        self._session.llm_provider_result = llm
        self._session.embedding_coverage = coverage
        self._session.embedding_reconciliation = reconciliation
        self._sync_embedding_vocabulary_selection()
        self._show_current_state(context)

    def _finish_connection_checks(self, results, context: PageContext) -> None:
        self._session.connection_results = tuple(results)
        self._show_current_state(context)

    def _graph_is_ready(self) -> bool:
        result = self._graph_readiness()
        return result is not None and result.connected and not result.has_warnings

    def _trigram_available(self) -> bool:
        result = self._graph_target_result("database.groundworkers")
        target = next(
            (
                item
                for item in resolve_database_targets(self._session.configuration)
                if item.key == "database.groundworkers"
            ),
            None,
        )
        return bool(
            target is not None
            and target.connection_url.startswith(("postgresql", "postgres"))
            and result is not None
            and result.connected
            and not any(
                diagnostic.code == "trigram_indexes_unchecked"
                for diagnostic in result.diagnostics
            )
        )

    def _graph_target_result(self, target_key: str):
        return next(
            (result for result in self._session.connection_results if result.target_key == target_key),
            None,
        )

    def _performance_can_prepare(self) -> bool:
        coverage = self._session.embedding_coverage
        embedding_available = bool(
            coverage is not None
            and coverage.coverage.available
            and coverage.index.registered
            and coverage.configuration.backend == "pgvector"
        )
        return self._trigram_available() or embedding_available

    def _integration_output(self):
        return build_integration_output(self._session.configuration)

    def _integration_ready(self) -> bool:
        return self._session.databases_connected and self._integration_output() is not None

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
        if self._selected_section == OVERVIEW_SECTION:
            context.surface.show_detail(
                self.route.key,
                TextView(
                    title="Readiness",
                    body="",
                ),
            )
            return
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
        if self._selected_section == RUNS_SECTION:
            run = next(
                (
                    item
                    for item in self._runs.store.list()
                    if item.run_id == self._selected_run_id
                ),
                None,
            )
            if run is None:
                detail = TextView(
                    title="Maintenance run",
                    body="Select a run to inspect its current step and recent log output.",
                )
            else:
                step_index = _selected_log_step(run)
                tail = (
                    self._runs.store.tail_log(run.run_id, step=step_index)
                    if step_index is not None
                    else ""
                )
                tail = format_progress_tail(tail)
                step_label = (
                    f"{step_index + 1}/{run.total} · "
                    f"{run.steps[step_index].spec.key}"
                    if step_index is not None
                    else "No step has started"
                )
                detail = TextView(
                    title=f"Maintenance run · {run.run_id}",
                    body=(
                        f"Status: {run.status.value}\n"
                        f"Step: {step_label}\n\n"
                        f"{tail or 'No log output for this step.'}"
                    ),
                )
            context.surface.show_detail(self.route.key, detail)
            return
        context.surface.show_detail(
            self.route.key,
            TextView(title=self._selected_section_title(), body=""),
        )

    def _selected_section_title(self) -> str:
        return SECTION_TITLES.get(self._selected_section, "Setup")

    def _handle_run_action(self, action_key: str, context: PageContext) -> None:
        if self._selected_run_id is None:
            context.notify("Select a maintenance run first.", severity="warning")
            return
        try:
            if action_key == "runs.cancel":
                self._runs.store.cancel(self._selected_run_id)
                context.notify("Cancellation requested.")
            elif action_key == "runs.retry":
                started = MaintenanceRunner(self._runs.store).retry(
                    self._selected_run_id
                )
                context.notify(f"Retry run {started.run_id} started.")
            elif action_key == "runs.postflight":
                started = MaintenanceRunner(self._runs.store).rerun_postflight(
                    self._selected_run_id
                )
                context.notify(f"Postflight run {started.run_id} started.")
            else:
                commands = self._runs.store.export_commands(self._selected_run_id)
                context.notify("\n".join(commands))
        except (KeyError, ValueError, RuntimeError) as exc:
            context.notify(str(exc), severity="warning")
        self._show_current_state(context)

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
    """Load embedding configuration when the optional embedding support exists."""

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
    """Check the embedding model when the optional embedding support exists."""

    try:
        from groundworkers.application.setup.embedding_reconciliation import (
            verify_embedding_model as verify,
        )
    except ImportError:
        return None
    return verify(snapshot)


def _selected_log_step(run) -> int | None:
    if run.current_step is not None:
        return run.current_step
    return next(
        (
            index
            for index in range(len(run.steps) - 1, -1, -1)
            if run.steps[index].log_path is not None
        ),
        None,
    )
