"""Verbatim tool-prompt text ported from Claude Code.

Upstream source: ``/c/cc-src/src/tools/<Tool>/prompt.ts``. Each entry is
what CC's own prompt-builder would render for its tool registry. CC's
runtime-dynamic parts (sandbox mode selection, undercover-mode text,
git-instructions env gating) are flattened to the defaults Nellie ships.

Tool-name references are rewritten from CC's capitalised names (``Read``,
``Bash``, etc.) to Nellie's lowercase registry names (``read``, ``bash``)
so the text matches what the model sees in tool-call schemas.

Consumed by ``karna/tools/base.py`` (via the ``cc_prompt`` class field)
and ``karna/prompts/tool_descriptions.py``. When a tool has no CC
equivalent, leave its ``cc_prompt`` empty and the fallback guidance in
``tool_descriptions.py`` takes over.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# read — FileReadTool/prompt.ts renderPromptTemplate()
# ---------------------------------------------------------------------------
READ = """Reads a file from the local filesystem. You can access any file directly by using this tool.
Assume this tool is able to read all files on the machine. If the User provides a path to a file assume that path is valid. It is okay to read a file that does not exist; an error will be returned.

Usage:
- The file_path parameter must be an absolute path, not a relative path
- By default, it reads up to 2000 lines starting from the beginning of the file
- When you already know which part of the file you need, only read that part. This can be important for larger files.
- Results are returned using cat -n format, with line numbers starting at 1
- This tool allows Nellie to read images (eg PNG, JPG, etc). When reading an image file the contents are presented visually as Nellie is a multimodal LLM.
- This tool can read PDF files (.pdf). For large PDFs (more than 10 pages), you MUST provide the pages parameter to read specific page ranges (e.g., pages: "1-5"). Reading a large PDF without the pages parameter will fail. Maximum 20 pages per request.
- This tool can read Jupyter notebooks (.ipynb files) and returns all cells with their outputs, combining code, text, and visualizations.
- This tool can only read files, not directories. To read a directory, use an ls command via the bash tool.
- You will regularly be asked to read screenshots. If the user provides a path to a screenshot, ALWAYS use this tool to view the file at the path. This tool will work with all temporary file paths.
- If you read a file that exists but has empty contents you will receive a system reminder warning in place of file contents."""

# ---------------------------------------------------------------------------
# write — FileWriteTool/prompt.ts getWriteToolDescription()
# ---------------------------------------------------------------------------
WRITE = """Writes a file to the local filesystem.

Usage:
- This tool will overwrite the existing file if there is one at the provided path.
- If this is an existing file, you MUST use the read tool first to read the file's contents. This tool will fail if you did not read the file first.
- Prefer the edit tool for modifying existing files — it only sends the diff. Only use this tool to create new files or for complete rewrites.
- NEVER create documentation files (*.md) or README files unless explicitly requested by the User.
- Only use emojis if the user explicitly requests it. Avoid writing emojis to files unless asked."""

# ---------------------------------------------------------------------------
# edit — FileEditTool/prompt.ts getDefaultEditDescription()
# ---------------------------------------------------------------------------
EDIT = """Performs exact string replacements in files.

Usage:
- You must use your `read` tool at least once in the conversation before editing. This tool will error if you attempt an edit without reading the file.
- When editing text from Read tool output, ensure you preserve the exact indentation (tabs/spaces) as it appears AFTER the line number prefix. The line number prefix format is: line number + tab. Everything after that is the actual file content to match. Never include any part of the line number prefix in the old_string or new_string.
- ALWAYS prefer editing existing files in the codebase. NEVER write new files unless explicitly required.
- Only use emojis if the user explicitly requests it. Avoid adding emojis to files unless asked.
- The edit will FAIL if `old_string` is not unique in the file. Either provide a larger string with more surrounding context to make it unique or use `replace_all` to change every instance of `old_string`.
- Use `replace_all` for replacing and renaming strings across the file. This parameter is useful if you want to rename a variable for instance."""

# ---------------------------------------------------------------------------
# grep — GrepTool/prompt.ts getDescription()
# ---------------------------------------------------------------------------
GREP = """A powerful search tool built on ripgrep

  Usage:
  - ALWAYS use grep for search tasks. NEVER invoke `grep` or `rg` as a bash command. The grep tool has been optimized for correct permissions and access.
  - Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+")
  - Filter files with glob parameter (e.g., "*.js", "**/*.tsx") or type parameter (e.g., "js", "py", "rust")
  - Output modes: "content" shows matching lines, "files_with_matches" shows only file paths (default), "count" shows match counts
  - Use the task tool for open-ended searches requiring multiple rounds
  - Pattern syntax: Uses ripgrep (not grep) - literal braces need escaping (use `interface\\{\\}` to find `interface{}` in Go code)
  - Multiline matching: By default patterns match within single lines only. For cross-line patterns like `struct \\{[\\s\\S]*?field`, use `multiline: true`
"""

# ---------------------------------------------------------------------------
# glob — GlobTool/prompt.ts DESCRIPTION
# ---------------------------------------------------------------------------
GLOB = """- Fast file pattern matching tool that works with any codebase size
- Supports glob patterns like "**/*.js" or "src/**/*.ts"
- Returns matching file paths sorted by modification time
- Use this tool when you need to find files by name patterns
- When you are doing an open ended search that may require multiple rounds of globbing and grepping, use the task tool instead"""

# ---------------------------------------------------------------------------
# bash — BashTool/prompt.ts getSimplePrompt() rendered for Nellie config:
#   embedded = False  (Nellie has grep/glob tools)
#   MONITOR_TOOL feature on (Nellie registers a monitor tool)
#   background_note included (run_in_background supported)
# ---------------------------------------------------------------------------
BASH = """Executes a given bash command and returns its output.

The working directory persists between commands, but shell state does not. The shell environment is initialized from the user's profile (bash or zsh).

IMPORTANT: Avoid using this tool to run `find`, `grep`, `cat`, `head`, `tail`, `sed`, `awk`, or `echo` commands, unless explicitly instructed or after you have verified that a dedicated tool cannot accomplish your task. Instead, use the appropriate dedicated tool as this will provide a much better experience for the user:

 - File search: Use glob (NOT find or ls)
 - Content search: Use grep (NOT grep or rg)
 - Read files: Use read (NOT cat/head/tail)
 - Edit files: Use edit (NOT sed/awk)
 - Write files: Use write (NOT echo >/cat <<EOF)
 - Communication: Output text directly (NOT echo/printf)
While the bash tool can do similar things, it's better to use the built-in tools as they provide a better user experience and make it easier to review tool calls and give permission.

# Instructions
 - If your command will create new directories or files, first use this tool to run `ls` to verify the parent directory exists and is the correct location.
 - Always quote file paths that contain spaces with double quotes in your command (e.g., cd "path with spaces/file.txt")
 - Try to maintain your current working directory throughout the session by using absolute paths and avoiding usage of `cd`. You may use `cd` if the User explicitly requests it.
 - You may specify an optional timeout in milliseconds (up to 600000ms / 10 minutes). By default, your command will timeout after 120000ms (2 minutes).
 - You can use the `run_in_background` parameter to run the command in the background. Only use this if you don't need the result immediately and are OK being notified when the command completes later. You do not need to check the output right away - you'll be notified when it finishes. You do not need to use '&' at the end of the command when using this parameter.
 - When issuing multiple commands:
  - If the commands are independent and can run in parallel, make multiple bash tool calls in a single message. Example: if you need to run "git status" and "git diff", send a single message with two bash tool calls in parallel.
  - If the commands depend on each other and must run sequentially, use a single bash call with '&&' to chain them together.
  - Use ';' only when you need to run commands sequentially but don't care if earlier commands fail.
  - DO NOT use newlines to separate commands (newlines are ok in quoted strings).
 - For git commands:
  - Prefer to create a new commit rather than amending an existing commit.
  - Before running destructive operations (e.g., git reset --hard, git push --force, git checkout --), consider whether there is a safer alternative that achieves the same goal. Only use destructive operations when they are truly the best approach.
  - Never skip hooks (--no-verify) or bypass signing (--no-gpg-sign, -c commit.gpgsign=false) unless the user has explicitly asked for it. If a hook fails, investigate and fix the underlying issue.
 - Avoid unnecessary `sleep` commands:
  - Do not sleep between commands that can run immediately — just run them.
  - Use the monitor tool to stream events from a background process (each stdout line is a notification). For one-shot "wait until done," use bash with run_in_background instead.
  - If your command is long running and you would like to be notified when it finishes — use `run_in_background`. No sleep needed.
  - Do not retry failing commands in a sleep loop — diagnose the root cause.
  - If waiting for a background task you started with `run_in_background`, you will be notified when it completes — do not poll.
  - `sleep N` as the first command with N ≥ 2 is blocked. If you need a delay (rate limiting, deliberate pacing), keep it under 2 seconds."""

# ---------------------------------------------------------------------------
# web_fetch — WebFetchTool/prompt.ts DESCRIPTION
# ---------------------------------------------------------------------------
WEB_FETCH = """
- Fetches content from a specified URL and processes it using an AI model
- Takes a URL and a prompt as input
- Fetches the URL content, converts HTML to markdown
- Processes the content with the prompt using a small, fast model
- Returns the model's response about the content
- Use this tool when you need to retrieve and analyze web content

Usage notes:
  - IMPORTANT: If an MCP-provided web fetch tool is available, prefer using that tool instead of this one, as it may have fewer restrictions.
  - The URL must be a fully-formed valid URL
  - HTTP URLs will be automatically upgraded to HTTPS
  - The prompt should describe what information you want to extract from the page
  - This tool is read-only and does not modify any files
  - Results may be summarized if the content is very large
  - Includes a self-cleaning 15-minute cache for faster responses when repeatedly accessing the same URL
  - When a URL redirects to a different host, the tool will inform you and provide the redirect URL in a special format. You should then make a new web_fetch request with the redirect URL to fetch the content.
  - For GitHub URLs, prefer using the gh CLI via bash instead (e.g., gh pr view, gh issue view, gh api).
"""

# ---------------------------------------------------------------------------
# web_search — WebSearchTool/prompt.ts getWebSearchPrompt()
# (month/year injected at runtime — see _build_web_search_prompt)
# ---------------------------------------------------------------------------
_WEB_SEARCH_TEMPLATE = """
- Allows Nellie to search the web and use the results to inform responses
- Provides up-to-date information for current events and recent data
- Returns search result information formatted as search result blocks, including links as markdown hyperlinks
- Use this tool for accessing information beyond your model's knowledge cutoff
- Searches are performed automatically within a single API call

CRITICAL REQUIREMENT - You MUST follow this:
  - After answering the user's question, you MUST include a "Sources:" section at the end of your response
  - In the Sources section, list all relevant URLs from the search results as markdown hyperlinks: [Title](URL)
  - This is MANDATORY - never skip including sources in your response
  - Example format:

    [Your answer here]

    Sources:
    - [Source Title 1](https://example.com/1)
    - [Source Title 2](https://example.com/2)

Usage notes:
  - Domain filtering is supported to include or block specific websites
  - Web search is only available in the US

IMPORTANT - Use the correct year in search queries:
  - The current month is {month_year}. You MUST use this year when searching for recent information, documentation, or current events.
  - Example: If the user asks for "latest React docs", search for "React documentation" with the current year, NOT last year
"""


def _build_web_search_prompt() -> str:
    """Render the WebSearch prompt with the current month/year injected.

    Mirrors CC's ``getWebSearchPrompt()`` which calls ``getLocalMonthYear()``
    at render time — the model benefits from an explicit current-date anchor
    when reasoning about "latest" queries.
    """
    from datetime import datetime

    return _WEB_SEARCH_TEMPLATE.format(month_year=datetime.now().strftime("%B %Y"))


WEB_SEARCH = _build_web_search_prompt()

# ---------------------------------------------------------------------------
# notebook — NotebookEditTool/prompt.ts PROMPT
# ---------------------------------------------------------------------------
NOTEBOOK = """Completely replaces the contents of a specific cell in a Jupyter notebook (.ipynb file) with new source. Jupyter notebooks are interactive documents that combine code, text, and visualizations, commonly used for data analysis and scientific computing. The notebook_path parameter must be an absolute path, not a relative path. The cell_number is 0-indexed. Use edit_mode=insert to add a new cell at the index specified by cell_number. Use edit_mode=delete to delete the cell at the index specified by cell_number."""

# ---------------------------------------------------------------------------
# task — TaskCreateTool/prompt.ts getPrompt()
# (agent swarms enabled in Nellie multi-instance setup — see CLAUDE.md)
# ---------------------------------------------------------------------------
TASK = """Use this tool to create a structured task list for your current coding session. This helps you track progress, organize complex tasks, and demonstrate thoroughness to the user.
It also helps the user understand the progress of the task and overall progress of their requests.

## When to Use This Tool

Use this tool proactively in these scenarios:

- Complex multi-step tasks - When a task requires 3 or more distinct steps or actions
- Non-trivial and complex tasks - Tasks that require careful planning or multiple operations and potentially assigned to teammates
- Plan mode - When using plan mode, create a task list to track the work
- User explicitly requests todo list - When the user directly asks you to use the todo list
- User provides multiple tasks - When users provide a list of things to be done (numbered or comma-separated)
- After receiving new instructions - Immediately capture user requirements as tasks
- When you start working on a task - Mark it as in_progress BEFORE beginning work
- After completing a task - Mark it as completed and add any new follow-up tasks discovered during implementation

## When NOT to Use This Tool

Skip using this tool when:
- There is only a single, straightforward task
- The task is trivial and tracking it provides no organizational benefit
- The task can be completed in less than 3 trivial steps
- The task is purely conversational or informational

NOTE that you should not use this tool if there is only one trivial task to do. In this case you are better off just doing the task directly.

## Task Fields

- **subject**: A brief, actionable title in imperative form (e.g., "Fix authentication bug in login flow")
- **description**: What needs to be done
- **activeForm** (optional): Present continuous form shown in the spinner when the task is in_progress (e.g., "Fixing authentication bug"). If omitted, the spinner shows the subject instead.

All tasks are created with status `pending`.

## Tips

- Create tasks with clear, specific subjects that describe the outcome
- After creating tasks, use TaskUpdate to set up dependencies (blocks/blockedBy) if needed
- Include enough detail in the description for another agent to understand and complete the task
- New tasks are created with status 'pending' and no owner - use TaskUpdate with the `owner` parameter to assign them
- Check TaskList first to avoid creating duplicate tasks
"""


# ---------------------------------------------------------------------------
# Registry — keyed by Nellie tool name
# ---------------------------------------------------------------------------
CC_TOOL_PROMPTS: dict[str, str] = {
    "read": READ,
    "write": WRITE,
    "edit": EDIT,
    "grep": GREP,
    "glob": GLOB,
    "bash": BASH,
    "web_fetch": WEB_FETCH,
    "web_search": WEB_SEARCH,
    "notebook": NOTEBOOK,
    "task": TASK,
}
