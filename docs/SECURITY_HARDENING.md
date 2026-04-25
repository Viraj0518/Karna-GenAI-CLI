# Security Hardening — `claude/alpha-nellie-tui-fixes-20260420`

> What a reviewer or operator needs to know about the security-relevant
> changes that landed on this branch. Source audit:
> [research/karna/NEW_TOOLS_AUDIT_20260420.md](../research/karna/NEW_TOOLS_AUDIT_20260420.md)
> (P0=1, P1=5, P2=4 at audit time; P0 and all P1s addressed here).

The four subsystems audited are new on `dev` — `browser`, `database`,
`comms`, and the notebook executor. Each now has a tool-level guard
layered on top of the shared primitives in
[karna/security/guards.py](../karna/security/guards.py).

---

## Notebook: refuses in-process cell evaluation

**File:** [karna/tools/notebook.py](../karna/tools/notebook.py)
**Commit:** [a9bed68](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/a9bed68) `sec(P0): remove unsandboxed fallback in notebook tool`

**What the fix does.** Cell execution goes through
[`_run_subprocess_execution`](../karna/tools/notebook.py), which tries
`jupyter nbconvert --execute` first, then `papermill`. If neither binary
is on `$PATH` the tool refuses to run the cell and returns a clear
installation hint. There is no branch that evaluates cell source inside
the Nellie host interpreter.

**Why it mattered.** A prior revision fell back to evaluating cell
source with Python's builtin `eval`/`exec` in-process. A model-authored
notebook could then run arbitrary code against the Nellie process —
reading environment variables (API keys live in `~/.karna/credentials/`
but keys in env vars would be exposed), importing networking modules,
or accessing the user's `$HOME`. Forcing a subprocess removes the
shared-memory attack surface; the user can further sandbox the
subprocess via their own `jupyter` install if they want stronger
isolation.

The follow-up [97515e1](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/97515e1)
surfaces stderr/exit-code diagnostics from the subprocess so a real
failure isn't hidden behind the generic refusal message.

---

## Database: parameterised queries + DSN SSRF guard + credential scrubbing

**File:** [karna/tools/database.py](../karna/tools/database.py)
**Commit:** [e77e029](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/e77e029) `sec(P0,P1): database tool — parameterised queries + DSN SSRF guard + cred scrub`

**What the fix does.** Three changes on the same tool:

1. **Parameterised queries.** The tool's schema now requires a `params`
   array alongside `sql`; all three backend adapters (`_SQLiteConn`,
   `_PostgreSQLConn`, `_MySQLConn`) pass the tuple through to the DB
   driver's parameter-binding API. The description explicitly tells
   the model to use `?` / `$1` / `%s` placeholders and never
   interpolate user values into the SQL string.
2. **DSN SSRF guard.** `connection_string` is parsed with `urlparse`,
   the hostname is extracted, and a synthesised `http://{host}/` probe
   is run through [`is_safe_url`](../karna/security/guards.py). A DSN
   pointing at `169.254.169.254`, an RFC-1918 address, or `localhost`
   is rejected before any driver touches it.
3. **Credential scrubbing on error.** Exception messages are passed
   through [`scrub_secrets`](../karna/security/guards.py) before
   surfacing. A failed `postgres://user:PASSWORD@host/db` connection no
   longer leaks the password into the tool result (and from there into
   the session DB, the logs, and potentially the next model turn).

**Why it mattered.** Without (1) the model could concatenate user input
into SQL and produce textbook injection — `id='1' OR '1'='1` returns
every row. Without (2) an attacker (or a confused model) could point
the tool at AWS metadata or an internal Postgres instance. Without (3)
a single connection failure would splash the DSN password through every
log line and conversation buffer downstream.

---

## Browser: per-request SSRF via `page.route()`

**File:** [karna/tools/browser.py](../karna/tools/browser.py)
**Commit:** [c4028ce](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/c4028ce) `sec(P1): browser tool — per-request SSRF guard via page.route()`

**What the fix does.** On browser startup the tool installs a
Playwright route handler via
[`page.route("**/*", _ssrf_route)`](../karna/tools/browser.py).
Every request the headless Chromium page makes — the initial
navigation, redirect targets (3xx chains), subresource fetches — is
re-checked against `is_safe_url`. Anything failing the check is
aborted with `route.abort("accessdenied")`.

**Why it mattered.** The old code validated the URL once via
`is_safe_url` before calling `page.goto(url)`. That lets two attacks
through:

- **DNS rebinding.** An attacker-controlled domain resolves to a public
  IP at validation time and flips to 127.0.0.1 (or 169.254.169.254)
  for the actual TCP connect. The one-shot check can't see the second
  DNS lookup.
- **Redirect chain.** A "safe" public URL can 302 to
  `http://169.254.169.254/latest/meta-data/` and Playwright will
  cheerfully follow it.

The route handler runs at the browser-network boundary, after DNS
resolution, for every request — so both holes close. A later Copilot
review fix ([97515e1](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/97515e1))
corrected the handler's arity to match Playwright's Python binding,
which always calls `handler(route, route.request)`.

---

## Comms: 1 MB message body cap

**File:** [karna/tools/comms.py](../karna/tools/comms.py)
**Commit:** [ce99d4d](https://github.com/virajsharma-karna/Karna-GenAI-CLI/commit/ce99d4d) `sec(P2): comms tool — 1 MB body cap on send + reply`

**What the fix does.** Both the `send` and `reply` actions now check
`len(body.encode("utf-8")) > _MAX_MESSAGE_BYTES` (1,000,000 bytes) and
reject the call with a clear error before touching disk. Every
legitimate agent-to-agent message we've observed is two orders of
magnitude below this cap.

**Why it mattered.** Without the cap a runaway or adversarial model
could write a 1 GB payload to another agent's inbox under
`~/.karna/comms/inbox/{agent}/` and exhaust the user's disk before any
other limit kicked in. The cap is deliberately looser than the typical
message size — enough headroom for pasted logs or structured plans,
tight enough that a bug can't hose the host.

---

## Shared primitives (context)

All four guards above build on functions in
[karna/security/guards.py](../karna/security/guards.py):

- [`is_safe_url`](../karna/security/guards.py) — SSRF check against
  private/reserved IPs, localhost, non-HTTP schemes.
- [`scrub_secrets`](../karna/security/guards.py) — regex removal of
  API keys (sk-, sk-or-v1-, sk-ant-), GitHub PATs, AWS access keys,
  PEM private keys, Bearer tokens, HuggingFace tokens.
- [`is_safe_path`](../karna/security/guards.py) — path-traversal guard
  used by the read/write/edit tools.
- [`check_dangerous_command`](../karna/security/guards.py) — shell
  pattern matcher used by [karna/agents/safety.py](../karna/agents/safety.py)
  and [karna/tools/bash.py](../karna/tools/bash.py).

If you're adding a new tool that talks to the network, opens a
database, or writes attacker-controlled bytes to disk, wire these in
at the tool boundary as well — don't rely on the generic
`pre_tool_check` in [karna/agents/safety.py](../karna/agents/safety.py)
alone. It catches obvious cases; the tool-specific guards above catch
the class-of-attack that only makes sense once you know what the tool
does.

---

## Still open (from the audit)

These P2 items from the audit were judged lower impact and left for a
follow-up pass. Listed here so a reviewer isn't surprised:

- **Document zip-bomb DoS** ([tools/document.py](../karna/tools/document.py)) — `openpyxl.load_workbook()` decompresses the entire zip into memory. Mitigated by output truncation (50K chars) but an attacker can still force a large alloc before truncation. A future fix should stream-parse or apply a size cap at read time.
- **Prompt injection via RAG** ([karna/rag/store.py](../karna/rag/store.py)) — retrieved text is injected into the system prompt without sanitisation. Known limitation for all RAG systems; should be documented in the RAG guidelines when user-facing docs ship.
- **RAG telemetry claim** — `SentenceTransformerEmbedder` downloads ~384 MB from Hugging Face on first use. The privacy notice in [karna/__init__.py](../karna/__init__.py) and [README.md](../README.md) should clarify this when the RAG optional-install becomes a documented feature.
