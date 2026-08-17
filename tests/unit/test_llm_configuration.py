from __future__ import annotations

from pathlib import Path

from oa_configurator import save_stack_config

from groundworkers.application.setup.configuration import load_configuration
from groundworkers.application.setup.llm_configuration import (
    LlmConfigurationDraft,
    apply_llm_configuration,
    draft_from_snapshot,
)
from tests.support.stack_config import build_cdm_stack


def test_llm_configuration_reads_and_writes_plain_groundworkers_mapping(
    tmp_path: Path,
) -> None:
    path = tmp_path / "config.toml"
    save_stack_config(
        build_cdm_stack(
            groundworkers={
                "llm": {
                    "enabled": True,
                    "provider": "ollama",
                    "api_base": "http://localhost:11434/v1",
                    "default_model_name": "old-model",
                }
            }
        ),
        path,
    )
    snapshot = load_configuration(config_path=path)

    assert draft_from_snapshot(snapshot).default_model_name == "old-model"

    result = apply_llm_configuration(
        snapshot,
        LlmConfigurationDraft(
            provider="ollama",
            api_base="http://localhost:11434/v1",
            default_model_name="new-model",
        ),
    )

    tool = result.save_result.snapshot.stack.tools["groundworkers"]
    assert tool["cdm_db"] == "cdm_db"
    assert tool["llm"]["default_model_name"] == "new-model"
    assert result.changed_fields == ("tools",)
