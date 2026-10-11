from pathlib import Path

from oa_configurator import save_stack_config

from groundworkers.tui.state import SetupSession
from groundworkers.tui.wizards.performance_maintenance import (
    PerformanceMaintenanceWizardController,
)
from tests.support.stack_config import build_cdm_stack


def test_postgres_trigram_action_is_available(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    save_stack_config(build_cdm_stack(), config_path)
    controller = PerformanceMaintenanceWizardController(
        SetupSession(config_path=config_path),
        embedding_coverage=None,
        trigram_available=True,
    )

    values = controller.start().values

    assert values["create_trigram_indexes"] is True
    assert values["create_normalized_indexes"] is True
    assert values["create_embedding_index"] is False
