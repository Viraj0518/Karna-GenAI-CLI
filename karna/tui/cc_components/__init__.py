"""CC-ported UI components — skinned for Nellie.

Mirrors Claude Code's TUI chrome: the one-line status bar, context progress
bar, token-warning panel, effort indicator, PR badge, cost-threshold alert,
and memory-usage indicator. Source files live under
`/c/cc-src/src/components/` — this package is a library-only port, no
runtime wiring.
"""

from karna.tui.cc_components.dialogs import (
    IdleReturnAction,
    confirm,
    exit_flow,
    idle_return,
    press_enter_to_continue,
    random_goodbye,
    render_ctrl_o_to_expand,
    render_keybinding_warnings,
    render_thinking_toggle,
    run_wizard,
)
from karna.tui.cc_components.input import (
    ScrollKeybindings,
    VimMode,
    VimTextInput,
    attach_configurable_shortcut_hint,
    render_clickable_image_ref,
)
from karna.tui.cc_components.permissions import (
    ToolPermissionChoice,
    prompt_api_key_trust,
    prompt_bypass_permissions,
    prompt_mcp_server_approval,
    prompt_tool_permission,
    render_permission_allowlist,
)
from karna.tui.cc_components.pickers import (
    Picker,
    pick_language,
    pick_model,
    pick_output_style,
    pick_theme,
)
from karna.tui.cc_components.search import (
    TagTabs,
    fuzzy_match,
    global_search,
    history_search,
    quick_open_file,
    render_search_box,
)
from karna.tui.cc_components.spinners import (
    BRAILLE_FRAMES,
    SPINNER_FRAMES,
    THINKING_GLYPH,
    TOOL_MESSAGES,
    pick_tool_message,
    render_agent_progress_line,
    render_bash_progress,
    render_coordinator_status,
    render_thinking_line,
    render_tool_loader,
)
from karna.tui.cc_components.status import (
    render_context_bar,
    render_cost_threshold_alert,
    render_effort_indicator,
    render_memory_usage,
    render_pr_badge,
    render_status_line,
    render_token_warning,
)
from karna.tui.cc_components.tasks import (
    render_agent_list,
    render_compact_summary,
    render_resume_task_prompt,
    render_session_background_hint,
    render_session_preview,
    render_task_list,
)

__all__ = [
    "render_status_line",
    "render_context_bar",
    "render_token_warning",
    "render_effort_indicator",
    "render_pr_badge",
    "render_cost_threshold_alert",
    "render_memory_usage",
    # Permissions / trust dialogs
    "ToolPermissionChoice",
    "prompt_tool_permission",
    "prompt_mcp_server_approval",
    "prompt_api_key_trust",
    "prompt_bypass_permissions",
    "render_permission_allowlist",
    # Task / agent / compact visuals
    "render_task_list",
    "render_compact_summary",
    "render_resume_task_prompt",
    "render_session_preview",
    "render_session_background_hint",
    "render_agent_list",
    # Search / history / quick-open dialogs
    "history_search",
    "global_search",
    "quick_open_file",
    "TagTabs",
    "render_search_box",
    "fuzzy_match",
    # Input primitives (input.py)
    "VimMode",
    "VimTextInput",
    "ScrollKeybindings",
    "attach_configurable_shortcut_hint",
    "render_clickable_image_ref",
    # Small dialogs (dialogs.py)
    "IdleReturnAction",
    "confirm",
    "press_enter_to_continue",
    "exit_flow",
    "random_goodbye",
    "idle_return",
    "render_thinking_toggle",
    "render_ctrl_o_to_expand",
    "run_wizard",
    "render_keybinding_warnings",
    # Spinners / tool-use loaders
    "BRAILLE_FRAMES",
    "SPINNER_FRAMES",
    "THINKING_GLYPH",
    "TOOL_MESSAGES",
    "pick_tool_message",
    "render_thinking_line",
    "render_tool_loader",
    "render_bash_progress",
    "render_agent_progress_line",
    "render_coordinator_status",
    # Picker dialogs
    "Picker",
    "pick_model",
    "pick_theme",
    "pick_output_style",
    "pick_language",
]
