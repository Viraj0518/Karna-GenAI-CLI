"""Grep tool — regex content search across files.


Features:
- Uses ``rg`` (ripgrep) when available, falls back to ``grep -rn``
- Supports: pattern, path, glob filter, output_mode, context lines
- Head limit on results (default 250)
- Case-insensitive search
"""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any

from karna.prompts.cc_tool_prompts import CC_TOOL_PROMPTS
from karna.tools.base import BaseTool

_DEFAULT_HEAD_LIMIT = 250


def _has_ripgrep() -> bool:
    return shutil.which("rg") is not None


class GrepTool(BaseTool):
    """Search file contents using regex patterns.

    Delegates to ripgrep (``rg``) when available for speed, otherwise
    falls back to ``grep -rn``.
    """

    name = "grep"
    description = "Search for a regex pattern across files. Uses ripgrep if available, otherwise grep -rn."
    cc_prompt = CC_TOOL_PROMPTS["grep"]
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regex pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": ("File or directory to search in. Defaults to current working directory."),
            },
            "glob": {
                "type": "string",
                "description": ('Glob pattern to filter files (e.g. "*.py", "*.{ts,tsx}").'),
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count"],
                "description": ("Output mode. Defaults to files_with_matches."),
            },
            "-i": {
                "type": "boolean",
                "description": "Case insensitive search.",
            },
            "-n": {
                "type": "boolean",
                "description": "Show line numbers (default true for content mode).",
            },
            "-B": {
                "type": "integer",
                "description": "Lines before each match (content mode only).",
            },
            "-A": {
                "type": "integer",
                "description": "Lines after each match (content mode only).",
            },
            "-C": {
                "type": "integer",
                "description": "Context lines before and after each match.",
            },
            "context": {
                "type": "integer",
                "description": "Alias for -C.",
            },
            "head_limit": {
                "type": "integer",
                "description": ("Limit output to first N entries. Defaults to 250. Pass 0 for unlimited."),
            },
            "offset": {
                "type": "integer",
                "description": "Skip first N entries before applying head_limit.",
            },
            "type": {
                "type": "string",
                "description": ("File type filter (rg --type). E.g. py, js, rust."),
            },
            "multiline": {
                "type": "boolean",
                "description": "Enable multiline matching (default false).",
            },
        },
        "required": ["pattern"],
    }

    async def execute(self, **kwargs: Any) -> str:  # noqa: C901
        pattern: str = kwargs["pattern"]
        search_path: str = kwargs.get("path", os.getcwd())
        glob_filter: str | None = kwargs.get("glob")
        output_mode: str = kwargs.get("output_mode", "files_with_matches")
        case_insensitive: bool = kwargs.get("-i", False)
        show_numbers: bool = kwargs.get("-n", True)
        before: int | None = kwargs.get("-B")
        after: int | None = kwargs.get("-A")
        context_c: int | None = kwargs.get("-C")
        context: int | None = kwargs.get("context")
        head_limit: int | None = kwargs.get("head_limit")
        offset: int = kwargs.get("offset", 0)
        file_type: str | None = kwargs.get("type")
        multiline: bool = kwargs.get("multiline", False)

        search_path = os.path.expanduser(search_path)

        use_rg = _has_ripgrep()

        if use_rg:
            args = self._build_rg_args(
                pattern=pattern,
                search_path=search_path,
                glob_filter=glob_filter,
                output_mode=output_mode,
                case_insensitive=case_insensitive,
                show_numbers=show_numbers,
                before=before,
                after=after,
                context_c=context_c,
                context=context,
                file_type=file_type,
                multiline=multiline,
            )
            cmd = args
        else:
            cmd = self._build_grep_args(
                pattern=pattern,
                search_path=search_path,
                output_mode=output_mode,
                case_insensitive=case_insensitive,
            )

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(),
                timeout=30,
            )
        except asyncio.TimeoutError:
            return "[error] Search timed out after 30s"
        except FileNotFoundError:
            return "[error] Neither rg nor grep found on this system."
        except Exception as exc:
            return f"[error] {exc}"

        stdout = stdout_bytes.decode("utf-8", errors="replace")

        # rg returns exit 1 for no matches (not an error)
        if proc.returncode not in (0, 1):
            stderr = stderr_bytes.decode("utf-8", errors="replace")
            return f"[error] Search failed (exit {proc.returncode}): {stderr}"

        if not stdout.strip():
            return "No matches found"

        lines = stdout.rstrip("\n").split("\n")

        # Apply offset + head_limit
        effective_limit = head_limit if head_limit is not None else _DEFAULT_HEAD_LIMIT
        if effective_limit == 0:
            # Unlimited
            selected = lines[offset:]
            truncated = False
        else:
            selected = lines[offset : offset + effective_limit]
            truncated = len(lines) - offset > effective_limit

        result = "\n".join(selected)

        if truncated:
            result += f"\n\n[Results truncated — showing {effective_limit} of {len(lines) - offset} entries]"

        return result

    # ------------------------------------------------------------------ #
    #  ripgrep arg builder
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_rg_args(
        *,
        pattern: str,
        search_path: str,
        glob_filter: str | None,
        output_mode: str,
        case_insensitive: bool,
        show_numbers: bool,
        before: int | None,
        after: int | None,
        context_c: int | None,
        context: int | None,
        file_type: str | None,
        multiline: bool,
    ) -> list[str]:
        args = ["rg", "--hidden"]

        # Exclude VCS directories
        for d in (".git", ".svn", ".hg", ".bzr", ".jj"):
            args.extend(["--glob", f"!{d}"])

        args.extend(["--max-columns", "500"])

        if multiline:
            args.extend(["-U", "--multiline-dotall"])

        if case_insensitive:
            args.append("-i")

        if output_mode == "files_with_matches":
            args.append("-l")
        elif output_mode == "count":
            args.append("-c")

        if show_numbers and output_mode == "content":
            args.append("-n")

        # Context flags
        if output_mode == "content":
            if context is not None:
                args.extend(["-C", str(context)])
            elif context_c is not None:
                args.extend(["-C", str(context_c)])
            else:
                if before is not None:
                    args.extend(["-B", str(before)])
                if after is not None:
                    args.extend(["-A", str(after)])

        # Pattern (protect leading dashes)
        if pattern.startswith("-"):
            args.extend(["-e", pattern])
        else:
            args.append(pattern)

        if file_type:
            args.extend(["--type", file_type])

        if glob_filter:
            for g in glob_filter.split():
                args.extend(["--glob", g])

        args.append(search_path)
        return args

    # ------------------------------------------------------------------ #
    #  grep fallback arg builder
    # ------------------------------------------------------------------ #

    @staticmethod
    def _build_grep_args(
        *,
        pattern: str,
        search_path: str,
        output_mode: str,
        case_insensitive: bool,
    ) -> list[str]:
        args = ["grep", "-rn"]
        if case_insensitive:
            args.append("-i")
        if output_mode == "files_with_matches":
            args.append("-l")
        elif output_mode == "count":
            args.append("-c")
        args.extend([pattern, search_path])
        return args
