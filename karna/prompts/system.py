"""System prompt builder — model-agnostic core with per-model adaptations.

This is the most critical module in Karna.  It assembles the system
prompt from identity, tool docs, behavioral guidelines, and context
sections, then optionally adapts it for the target model/provider.

Design principles (learned from cc-src):
- The base prompt works for ANY model — no provider lock-in.
- Per-model adaptations are additive tweaks, not rewrites.
- Context sections are injected in priority order so we can trim
  to fit a token budget without losing the essentials.
- Tool documentation is auto-generated from the tool registry so
  new tools get prompt coverage for free.

Ported from cc-src system prompt patterns with attribution to the
Anthropic Claude Code codebase.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from karna.prompts.tool_descriptions import generate_tool_docs
from karna.tokens import count_tokens

if TYPE_CHECKING:
    from karna.config import KarnaConfig
    from karna.tools.base import BaseTool


# ------------------------------------------------------------------ #
#  Template loading
# ------------------------------------------------------------------ #

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# Cache loaded templates in-process.
_template_cache: dict[str, str] = {}


def _load_template(name: str) -> str:
    """Load a prompt template by name (without .txt extension).

    Falls back to ``default.txt`` if the requested template doesn't exist.
    """
    if name in _template_cache:
        return _template_cache[name]

    path = _TEMPLATES_DIR / f"{name}.txt"
    if not path.exists():
        path = _TEMPLATES_DIR / "default.txt"

    text = path.read_text(encoding="utf-8")
    _template_cache[name] = text
    return text


# ------------------------------------------------------------------ #
#  Model / provider adaptation tables
# ------------------------------------------------------------------ #

MODEL_ADAPTATIONS: dict[str, dict[str, Any]] = {
    "anthropic": {
        "tool_format": "anthropic_native",  # uses Anthropic tool_use blocks
        "system_placement": "top_level",  # system as separate param
        "supports_cache": True,  # prompt caching
        "template": "anthropic",
    },
    "openai": {
        "tool_format": "openai_functions",  # uses function_call
        "system_placement": "first_message",  # system as first message
        "supports_cache": False,
        "template": "default",
    },
    "openrouter": {
        "tool_format": "openai_functions",  # OpenAI-compatible
        "system_placement": "first_message",
        "supports_cache": False,
        "template": "default",
    },
    "azure": {
        "tool_format": "openai_functions",
        "system_placement": "first_message",
        "supports_cache": False,
        "template": "default",
    },
    "local": {
        "tool_format": "openai_functions",
        "system_placement": "first_message",
        "supports_cache": False,
        "template": "weak_model",
    },
}

# Models considered "weak" that benefit from more explicit instructions.
# Prefix-matched against the model ID.
_WEAK_MODEL_PREFIXES = (
    "phi-",
    "qwen2-",
    "gemma-",
    "llama-3.2-1b",
    "llama-3.2-3b",
    "mistral-7b",
    "deepseek-r1-distill",
)


def _is_weak_model(model: str) -> bool:
    """Return True if the model is considered 'weak' and needs
    more explicit instructions."""
    model_lower = model.lower()
    return any(model_lower.startswith(p) or f"/{p}" in model_lower for p in _WEAK_MODEL_PREFIXES)


# ------------------------------------------------------------------ #
#  Context section builders
# ------------------------------------------------------------------ #


def _build_context_sections(
    project_context: str | None,
    git_context: str | None,
    memory_context: str | None,
    custom_instructions: str | None,
) -> list[tuple[str, str, int]]:
    """Build context sections as (label, content, priority) tuples.

    Priority determines trimming order (1 = keep always, higher = trim first).
    """
    sections: list[tuple[str, str, int]] = []

    if custom_instructions:
        sections.append(("Custom Instructions", custom_instructions, 2))

    if project_context:
        sections.append(("Project Context", project_context, 3))

    if git_context:
        sections.append(("Git Context", git_context, 4))

    if memory_context:
        sections.append(("Memory", memory_context, 5))

    return sections


def _format_context_sections(sections: list[tuple[str, str, int]]) -> str:
    """Format context sections into prompt text, sorted by priority."""
    if not sections:
        return ""

    sorted_sections = sorted(sections, key=lambda s: s[2])
    parts: list[str] = []
    for label, content, _priority in sorted_sections:
        parts.append(f"# {label}\n{content}")

    return "\n\n".join(parts)


# ------------------------------------------------------------------ #
#  Token budget estimation
# ------------------------------------------------------------------ #


def _estimate_tokens(text: str) -> int:
    """Token count via tiktoken when available, else len//4 fallback."""
    return count_tokens(text)


def _trim_to_budget(
    base_prompt: str,
    context_sections: list[tuple[str, str, int]],
    max_tokens: int,
) -> str:
    """Trim context sections (highest priority number first) to fit budget.

    The base prompt (identity + tools + guidelines) is never trimmed.
    Context sections are removed in reverse priority order until we fit.
    """
    base_tokens = _estimate_tokens(base_prompt)
    if base_tokens >= max_tokens:
        # Base prompt alone exceeds budget — return it anyway,
        # the model will have to cope.
        return base_prompt

    remaining = max_tokens - base_tokens

    # Sort by priority descending so we trim least-important first
    sorted_sections = sorted(context_sections, key=lambda s: -s[2])
    kept: list[tuple[str, str, int]] = []

    for section in sorted_sections:
        section_tokens = _estimate_tokens(section[1])
        if section_tokens <= remaining:
            kept.append(section)
            remaining -= section_tokens
        # else: skip this section entirely

    if not kept:
        return base_prompt

    context_text = _format_context_sections(kept)
    return base_prompt.replace("{context_sections}", context_text)


# ------------------------------------------------------------------ #
#  Model-specific adaptation
# ------------------------------------------------------------------ #


def adapt_for_model(base_prompt: str, provider: str, model: str) -> str:
    """Add model-specific instructions to the base prompt.

    For Claude: mention XML tag support and thinking capabilities.
    For weaker models: add explicit tool-selection reminders.
    For GPT models: note JSON-mode for structured output.
    """
    additions: list[str] = []

    provider_lower = provider.lower()
    model_lower = model.lower()

    if provider_lower == "anthropic" or "claude" in model_lower:
        additions.append(
            "# Model Notes\n"
            "You are running on a Claude model. You can use XML tags "
            "for structured output when helpful. Tool calls use native "
            "tool_use blocks — do not simulate them with text."
        )
    elif "gpt" in model_lower or "o3" in model_lower or "o1" in model_lower:
        additions.append(
            "# Model Notes\n"
            "Tool calls use function_call format. For structured output, "
            "you can use JSON. Do not simulate tool calls with text — "
            "always use the provided function-calling mechanism."
        )

    if _is_weak_model(model):
        additions.append(
            "# Important Reminders\n"
            "You MUST use tools to complete tasks. Do not try to answer "
            "from memory when the answer requires reading files or running "
            "commands. Always use the `read` tool before the `edit` tool. "
            "Think step by step about which tool to use."
        )

    if additions:
        return base_prompt + "\n\n" + "\n\n".join(additions)

    return base_prompt


def get_adaptation(provider: str) -> dict[str, Any]:
    """Return the adaptation config for a provider.

    Falls back to a generic OpenAI-compatible config.
    """
    return MODEL_ADAPTATIONS.get(
        provider.lower(),
        MODEL_ADAPTATIONS["openai"],  # safe default
    )


# ------------------------------------------------------------------ #
#  Environment info
# ------------------------------------------------------------------ #


def _build_env_section() -> str:
    """Build the environment information section."""
    import platform

    cwd = os.getcwd()
    shell = os.environ.get("SHELL", "unknown")
    shell_name = "zsh" if "zsh" in shell else "bash" if "bash" in shell else shell

    # Check if cwd is a git repo
    is_git = (Path(cwd) / ".git").exists()

    uname = platform.uname()
    os_version = f"{uname.system} {uname.release}"

    return (
        f"# Environment\n"
        f"Working directory: {cwd}\n"
        f"Is directory a git repo: {'Yes' if is_git else 'No'}\n"
        f"Platform: {uname.system.lower()}\n"
        f"Shell: {shell_name}\n"
        f"OS Version: {os_version}"
    )


# ------------------------------------------------------------------ #
#  Main builder
# ------------------------------------------------------------------ #


def build_system_prompt(
    config: "KarnaConfig",
    tools: list["BaseTool"],
    project_context: str | None = None,
    git_context: str | None = None,
    memory_context: str | None = None,
    custom_instructions: str | None = None,
    *,
    max_tokens: int = 4000,
) -> str:
    """Build the complete system prompt within a token budget.

    This is the central entry point. It:
    1. Selects the right template for the provider/model
    2. Generates tool documentation from the registry
    3. Injects context sections in priority order
    4. Trims to fit the token budget
    5. Applies model-specific adaptations

    Priority order when trimming:
    1. Identity + tools (always include)
    2. Behavioral guidelines (always include)
    3. Custom instructions (include if fits)
    4. Project context (include if fits)
    5. Git context (include if fits)
    6. Memory context (include if fits, trimmed first)

    Parameters
    ----------
    config : KarnaConfig
        The active Karna configuration.
    tools : list[BaseTool]
        All registered tools to document in the prompt.
    project_context : str, optional
        Content from KARNA.md or .karna/project.toml.
    git_context : str, optional
        Branch, status, recent commits.
    memory_context : str, optional
        Relevant memories from ~/.karna/memory/.
    custom_instructions : str, optional
        User's personal preferences.
    max_tokens : int
        Maximum token budget for the prompt (default 4000).

    Returns
    -------
    str
        The assembled system prompt string.
    """
    provider = config.active_provider
    model = config.active_model

    # Determine template
    adaptation = get_adaptation(provider)
    template_name = adaptation.get("template", "default")

    # Override to weak_model template if model is detected as weak
    if _is_weak_model(model):
        template_name = "weak_model"

    template = _load_template(template_name)

    # Generate tool docs
    tool_docs = generate_tool_docs(tools)

    # Determine user name from environment
    user = os.environ.get("USER", os.environ.get("USERNAME", "user"))

    # Build context sections
    context_sections = _build_context_sections(
        project_context=project_context,
        git_context=git_context,
        memory_context=memory_context,
        custom_instructions=custom_instructions,
    )

    # Add environment info as a context section (priority 2, always kept)
    env_section = _build_env_section()
    context_sections.insert(0, ("Environment", env_section.replace("# Environment\n", ""), 1))

    # Fill template placeholders
    prompt = template.replace("{user}", user)
    prompt = prompt.replace("{tool_docs}", tool_docs)

    # Format and inject context sections with budget trimming
    prompt = _trim_to_budget(prompt, context_sections, max_tokens)

    # If {context_sections} placeholder wasn't consumed by trim (no trimming needed),
    # replace it now
    if "{context_sections}" in prompt:
        context_text = _format_context_sections(context_sections)
        prompt = prompt.replace("{context_sections}", context_text)

    # Apply model-specific adaptations
    prompt = adapt_for_model(prompt, provider, model)

    return prompt
