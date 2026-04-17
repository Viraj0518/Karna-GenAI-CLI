# Karna Error-Handling Audit — 2026-04-17

Scope: `karna/` tree only. Skipped tests, vendored code (none present).
Reviewer: gamma (Claude). Budget: 35 min.

Overall posture is better than feared for 4-hour speed-build code: bare
`except: pass` is rare, most excepts log something. The real problem is
a cluster of *silent fallbacks* in the compaction + config + credential +
auth paths that will cap a session or swallow corruption.

## CRITICAL — will silently corrupt state / hide crash loops

| # | File:line | Issue | Fix |
|---|---|---|---|
| C1 | `karna/compaction/compactor.py:178-193` | Broad except around `provider.complete()` swallows every compaction error, logs at `warning`, returns `conversation` **unchanged**. If the threshold is already exceeded, `should_compact` keeps returning True, provider keeps failing, conversation keeps growing. After 3 failures the breaker trips — but it is silent to the user. Net effect: cap of conversation forever with zero surface signal. | Bubble the first 2 exceptions up to the agent loop and render a user-visible banner on breaker trip (`OutputRenderer.print_warning`). Also record failure class separately (network vs. empty response) so network blips do not trip the breaker permanently. |
| C2 | `karna/compaction/compactor.py:44` + `_estimate_tokens` | Token estimate is `chars // 4`. Under-counts tool results with JSON / code. Combined with C1, a large tool output can skip past the 0.93 threshold *after* the check — provider 400s on context-length, C1 swallows it, breaker trips. | Use `TokenCounter.count` (already exists in `karna/tokens/counter.py`) with the active model; add a hard ceiling check on the provider response status so `context_length_exceeded` triggers an aggressive compact, not a silent skip. |
| C3 | `karna/config.py:60-77` `load_config` | `tomllib.loads(...)` and `KarnaConfig(**data)` are un-guarded. Malformed `~/.karna/config.toml` or a schema mismatch raises at first `karna` invocation — user gets a Python stack trace. Worse: the docstring promises "returning defaults if the file doesn't exist" but gives no defaults for malformed. | Wrap in `try/except (tomllib.TOMLDecodeError, ValidationError) as exc` and either (a) abort with a clear `[error] Config at {path} is invalid: {exc}. Move it aside or fix to continue.` or (b) load defaults **and print a visible warning** — do not silently reset. |
| C4 | `karna/auth/credentials.py:58-69` `load_credential` | If the credential file exists but is corrupt, `json.loads` raises uncaught. If it is *missing*, returns `{}` silently — `load_credential_pool` then returns an empty pool and the provider fails later with an opaque "no API key" in HTTP land. | On missing file: log at `info` with explicit "Run `karna auth login <provider>`" guidance. On `JSONDecodeError`: raise `CredentialError` with the path so the user knows which file to fix. |
| C5 | `karna/tools/mcp.py:266-272` `_load_configured_servers` | `tomllib.loads(raw.decode())` is unguarded. Any user with a broken TOML never gets MCP; `self._server_configs` silently stays `{}`, and later `_connect` says "No MCP server configured with name 'X'. Available: (none)" — misleading if the config does list X but failed to parse. | Try/except with a logger.error and surface the parse error into `_connect` return text. |
| C6 | `karna/tools/mcp.py:102-121` `MCPServerConnection.stop` + `:175-211` `_read_loop` | `self.process.stdin.close()` has `# type: ignore[union-attr]` but does not handle `BrokenPipeError` / `ConnectionResetError` when the server already died. A crashed MCP server at shutdown leaves zombie tasks and swallows the real death cause. Reader-loop crash at line 211 only logs `error`, never flips connection state, so `call()` waits full 30s on a dead server. | In `stop`, catch `(BrokenPipeError, ConnectionResetError, OSError)` around `stdin.close()`. In `_read_loop`'s broad except, cancel all `self._pending` futures with the exception so outstanding `call()`s fail fast instead of timing out. |

## HIGH — will obscure root cause during debugging

| # | File:line | Issue | Fix |
|---|---|---|---|
| H1 | `karna/hooks/builtins.py:82-85` | Broad except returning empty `HookResult()` on `git status` swallows *everything* including `OSError`, permission, and any bug in the decode. Users wondering why the dirty-tree banner never fires get no signal. | Narrow to `(OSError, asyncio.TimeoutError, subprocess.SubprocessError)`. |
| H2 | `karna/hooks/builtins.py:115-127` `auto_save_memory_hook` | Broad except + `logger.debug` — a real bug in `MemoryManager.auto_extract` (e.g. frontmatter write corruption) is invisible at the default log level. | `logger.warning` with `exc_info=True`; consider surfacing a one-line `[memory] auto-extract failed: ...` to user. |
| H3 | `karna/hooks/dispatcher.py:94-95` | `except KeyError: rendered = command` — a hook template with `{tool}` that cannot resolve *silently* runs the unformatted command. The hook author never learns their placeholder is wrong. | Log warning: `"Hook template %r missing key, falling back to raw command"`. |
| H4 | `karna/hooks/dispatcher.py:185-189` | Broad except then `continue` — broken hook doesn't abort, doesn't re-raise, doesn't surface to user. `PRE_TOOL_USE` guard hooks that crash **effectively allow the tool** — security-adjacent silent failure. | For `PRE_TOOL_USE` and `ON_ERROR`, escalate hook exceptions into `proceed=False` with message `"Hook {fn} crashed — blocking for safety"`. Non-guard hooks can keep the current behavior but log at `error`. |
| H5 | `karna/tools/web_fetch.py:96-97` + `:207-210` + `:298-299` | Three broad `except: pass` bodies. Robots fetch, trafilatura import, robots enforcement — all silently degrade. Permissive-on-failure for robots is defensible (commented), but the first two should log at `debug` with the exception so a broken optional dep is discoverable. | Replace `pass` with `logger.debug("...", exc_info=True)`. |
| H6 | `karna/context/git.py:101-116` `_run_git` | Returns `""` on **any** failure including `FileNotFoundError` (git missing). A user on a box without git gets an empty environment block and no hint. | Return `None`; caller distinguishes "no repo" from "git missing" and prints a one-time `[context] git not on PATH — skipping git context.` |
| H7 | `karna/tui/repl.py:143-144` | `except Exception: pass` around `git rev-parse`. Session record loses branch, user never knows. `subprocess.run(..., timeout=2)` is already there, narrow the except and log at debug. | `except (subprocess.SubprocessError, OSError, subprocess.TimeoutExpired): logger.debug(...)` |
| H8 | `karna/init.py:42-43` | `except Exception: pass` around pyproject parse. Malformed pyproject gives the user defaults with no hint. | Log warning with path. |
| H9 | `karna/agents/subagent.py:106-119` `_cleanup_worktree` | Two broad excepts — but the warnings have no `exc_info=True`. When `git worktree remove` fails (merge conflicts, dirty state), the user sees "Failed to remove worktree at X" with no reason. Next session re-uses the same hardcoded path `/tmp/karna-worktree-<name>` — will collide. | Add `exc_info=True`; randomize worktree path (e.g. `mkdtemp`) so collisions are impossible. |
| H10 | `karna/tools/mcp.py:309-312` | Broad except returning `"[error] Failed to start MCP server..."` eats the actual traceback. Bad command path vs. bad handshake vs. timeout all collapse to the same message. | Log at `error` with `exc_info=True` *before* returning the flattened message. |
| H11 | `karna/tools/notebook.py:360-361` | `exec` of notebook cell with broad except storing `f"Error: {exc}"` — no traceback in the captured output. Debugging a failing cell is miserable. | `import traceback; captured = traceback.format_exc()`. |
| H12 | `karna/memory/manager.py:226-227` | `except Exception: continue` silently drops unparseable memory files. A broken frontmatter means a memory is gone with zero notice. | Log warning with path + exception so the user can fix the file. |
| H13 | `karna/skills/loader.py:365-368` (and `:231-232`) | Persist-enabled-state write failure only logs warning. User toggles a skill and next session it is back on — "bug" report, no signal. | Return failure up; caller prints `[skills] could not persist enabled state: ...`. |

## MEDIUM — style, not functionally broken

| # | File:line | Note |
|---|---|---|
| M1 | `karna/providers/base.py:71-74` | `except RuntimeError: task_id = 0` — fine, but comment why (no running loop). |
| M2 | `karna/agents/safety.py:128-129`, `karna/security/guards.py:184-185` | `urlparse` broad except returning False — `urlparse` raises `ValueError` for bad ports, rarely anything else. Narrow to `ValueError`. |
| M3 | `karna/tui/output.py:147-148` | JSON pretty-printer fallback — fine, display-only. |
| M4 | `karna/tools/read.py:56-57` | Binary detection `except OSError: pass` — fine, returns False (not binary) which is safe. |
| M5 | `karna/tools/notebook.py:307-308`/`:323-324` | `unlink` `except OSError: pass` on temp output. OK but leaves litter; log at debug. |
| M6 | `karna/agents/loop.py:125-128` | Broad except at tool-execute is intentional (isolates one tool crash from the rest of the batch). Already logs with `logger.exception` — good. Only flag: `type(exc).__name__: {exc}` in the user message may leak internal types; consider mapping to a cleaner message for known exc classes. |
| M7 | `karna/config.py:86` `save_config` | `write_bytes` has no retry and no temp-file-then-rename. A crash mid-write corrupts config. Low probability, but `write_bytes` on Windows can race AV scanners. Use `path.with_suffix(".toml.tmp")` + `os.replace`. |
| M8 | `karna/tools/mcp.py:461` `_save_raw_config` | Same as M7 — atomic write would be safer. |

## Things done well (rare and worth calling out)

- `karna/providers/base.py:220-282` — retry loop is clean: narrow catches
  (`httpx.TimeoutException`, `httpx.HTTPStatusError`), explicit re-raise on
  non-retryable, jittered backoff, and **never logs request bodies**. All
  provider subclasses call `resp.raise_for_status()`.
- `karna/agents/loop.py:107-128` `_execute_tool` — layered catches
  (`asyncio.TimeoutError` / `PermissionError` / `FileNotFoundError` /
  fallback) each with a tailored user message and `logger.exception` on
  the fallback. Textbook.
- `karna/tools/mcp.py:127-155` `call` — explicit `TimeoutError`, pops
  pending future, propagates protocol-level error object. Minor bug
  (C6/H10 above) but the happy path is well-shaped.
- `karna/hooks/dispatcher.py:194-199` — checks that hook returned a
  `HookResult`, logs and skips otherwise. Good defensive typing.
- `karna/agents/loop.py:220-223` uses `return_exceptions=False` *because*
  `_execute_tool` already catches everything — this is the right call and
  avoids the "gather returns exceptions nobody inspects" trap.

## Priorities if you have 60 min to fix

1. C1 (compaction silent-loop) — biggest user pain, single function.
2. C3 / C4 (config + credential malformed paths) — onboarding killers.
3. H4 (pre-tool-use hook crash = tool allowed) — security-adjacent.
4. H9 (randomize worktree path) — subagent reliability.
