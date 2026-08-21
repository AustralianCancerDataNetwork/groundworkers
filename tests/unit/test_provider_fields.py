from __future__ import annotations

import pytest
from oa_configurator import ModelConfig, ProviderConfig, StackConfig
from omop_llm import supported_providers

from groundworkers.application.setup.configuration_provider import (
    _llm_fields,
    _model_fields,
)

FIELDS = pytest.mark.parametrize(
    ("build", "kind_key", "url_key"),
    (
        (_model_fields, "provider_kind", "base_url"),
        (_llm_fields, "llm_provider_kind", "llm_base_url"),
    ),
    ids=("embeddings", "chat"),
)


def _field(fields, key):
    return next(field for field in fields if field.key == key)


def _stack(provider_kind: str, base_url: str | None = None) -> StackConfig:
    return StackConfig(
        providers={"p": ProviderConfig(provider=provider_kind, base_url=base_url)},
        models={"m": ModelConfig(provider="p", model="x")},
        tools={"groundworkers": {"embedding_model_name": "m", "llm_model_name": "m"}},
    )


@FIELDS
def test_offered_providers_are_ones_any_llm_accepts(build, kind_key, url_key) -> None:
    """A hardcoded list previously offered 'openai-compatible', which omop-llm
    rejects at runtime. Deriving from supported_providers() makes that
    impossible rather than merely fixed."""
    offered = {choice.value for choice in _field(build(_stack("ollama")), kind_key).choices}

    assert offered == set(supported_providers())


@FIELDS
def test_ollama_defaults_to_its_well_known_endpoint(build, kind_key, url_key) -> None:
    default = _field(build(StackConfig(tools={"groundworkers": {}})), url_key).default

    assert default == "http://localhost:11434"


@FIELDS
def test_a_hosted_provider_gets_no_guessed_endpoint(build, kind_key, url_key) -> None:
    """Blank means 'use the provider's own default', which beats a wrong guess."""
    assert _field(build(_stack("openai")), url_key).default is None


@FIELDS
def test_a_stored_endpoint_always_wins(build, kind_key, url_key) -> None:
    stack = _stack("ollama", base_url="http://gpu-host:11434/v1")

    assert _field(build(stack), url_key).default == "http://gpu-host:11434/v1"


@FIELDS
def test_the_endpoint_is_optional_in_both_wizards(build, kind_key, url_key) -> None:
    assert _field(build(_stack("openai")), url_key).required is False
