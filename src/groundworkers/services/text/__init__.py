"""LLM-backed clinical text semantics.

This package keeps the public TextService surface small and stable while
separating result models, prompt definitions, and service orchestration into
modules that can grow independently.
"""

from .models import (
    DecomposeResult,
    DecomposeTerm,
    DisambiguateResult,
    Interpretation,
    MappingCleanupResult,
    NormalizeResult,
)
from .prompts import SYSTEM_PROMPTS, build_user_prompt
from .service import TextService

__all__ = [
    "SYSTEM_PROMPTS",
    "DecomposeResult",
    "DecomposeTerm",
    "DisambiguateResult",
    "Interpretation",
    "MappingCleanupResult",
    "NormalizeResult",
    "TextService",
    "build_user_prompt",
]
