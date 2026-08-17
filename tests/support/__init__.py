"""Shared test builders for the current stack schema."""

from tests.support.stack_config import (
    build_cdm_stack,
    build_embedding_stack,
    build_invalid_reference_stack,
)

__all__ = [
    "build_cdm_stack",
    "build_embedding_stack",
    "build_invalid_reference_stack",
]
