"""Karna system prompt engine.

Exports the main ``build_system_prompt()`` builder plus supporting
utilities for tool documentation and model adaptation.
"""

from karna.prompts.system import (
    MODEL_ADAPTATIONS,
    adapt_for_model,
    build_system_prompt,
    get_adaptation,
)
from karna.prompts.tool_descriptions import generate_tool_docs

__all__ = [
    "MODEL_ADAPTATIONS",
    "adapt_for_model",
    "build_system_prompt",
    "generate_tool_docs",
    "get_adaptation",
]
