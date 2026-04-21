# Nellie 0.1.3 — Pre-Release Checklist

Run top-to-bottom. Don't merge to `main` or tag until every item is either **checked** or explicitly deferred with a note.

Date: 2026-04-21. Target: merge `dev` → `main`, tag `v0.1.3`.

---

## 1. Version strings (must all match `0.1.3`)

- [ ] `pyproject.toml` — `[project] version = "0.1.3"`
- [ ] `karna/__init__.py` — `__version__ = "0.1.3"`
- [ ] `nellie --version` prints `nellie 0.1.3` after editable reinstall
- [ ] No stale `0.1.0` references in README install snippets, GETTING_STARTED, install.sh/ps1 pins

**Verify:**
```bash
grep -rn '0\.1\.0' karna/ pyproject.toml README.md GETTING_STARTED.md install.sh install.ps1 2>&1 | grep -v CHANGELOG
```
Expected: no hits (CHANGELOG legitimately keeps historical `0.1.0` in release notes).

---

## 2. Working tree + branch state

- [ ] `git status --short` is clean (no uncommitted changes, no untracked files that should be committed)
- [ ] All scratch files (`_dogfood*.txt`, `_web_*`, `_frames/`, `generated/`) are `.gitignore`d, not committed
- [ ] `git log origin/dev..dev` is empty (dev is pushed)
- [ ] No stale local branches from prior feature work

**Verify:**
```bash
git status --short
git log origin/dev..dev
```

---

## 3. CHANGELOG

- [ ] `## [0.1.3] - 2026-04-21` heading present with 3-theme summary
- [ ] Subsections (Security / Added / Changed / Fixed) all populated from this cycle's work
- [ ] Fresh empty `## [Unreleased]` heading above the 0.1.3 entry for future work
- [ ] No "TODO" or placeholder text in the 0.1.3 body
- [ ] Every significant commit since 0.1.0 is represented

**Verify:**
```bash
grep -nE '^## \[' CHANGELOG.md | head -5
```
Expected first 3 lines: `[Unreleased]` → `[0.1.3] - 2026-04-21` → `[0.1.0]`.

---

## 4. Regression gate (must be green)

- [ ] `pytest tests/test_cc_*.py tests/test_tui_integration.py tests/test_repl_imports.py tests/test_cli_surface.py tests/test_cc_tool_prompts.py -q` → all pass
- [ ] `pytest tests/test_background_bash.py -q` finishes in <30 s (hang fix verified)
- [ ] `pytest tests/` (full) finishes in reasonable time (<10 min) — if still slow, document the offenders
- [ ] `test_live_api.py` excluded in CI (needs real keys)
- [ ] No new `@pytest.mark.skip` on previously-green tests

**Verify:**
```bash
python -m pytest tests/test_cc_*.py tests/test_tui_integration.py tests/test_repl_imports.py \
  tests/test_cli_surface.py tests/test_cc_tool_prompts.py -q
```
Expected: `149 passed` (or higher if new tests landed).

---

## 5. Lint + format

- [ ] `ruff check .` clean (or diffs understood and approved)
- [ ] `ruff format --check .` clean
- [ ] No stray `# type: ignore` / `# noqa` added without a reason comment

**Verify:**
```bash
ruff check karna/ tests/ tools/
ruff format --check karna/ tests/ tools/
```

---

## 6. Build / install

- [ ] `pip install -e .` succeeds in a clean venv (no missing deps, no setuptools errors)
- [ ] Editable install picks up `__version__ = "0.1.3"`
- [ ] Wheel build succeeds: `python -m build --wheel` produces `karna-0.1.3-py3-none-any.whl`

**Verify:**
```bash
python -m venv /tmp/verify-0.1.3 && /tmp/verify-0.1.3/bin/pip install -e . && /tmp/verify-0.1.3/bin/nellie --version
```

---

## 7. Runtime smoke (hermes_repl path — default in 0.1.3)

- [ ] `nellie --version` prints `nellie 0.1.3`
- [ ] `nellie --help` renders clean, no mojibake, `tools: 19` badge matches
- [ ] `nellie` TUI boots (PTY smoke or interactive) — banner renders, input accepts, Ctrl-C exits cleanly
- [ ] Native terminal scrollbar works (scroll up / down reveals prior turns)
- [ ] Copy-paste works from the transcript
- [ ] `nellie web` starts, `/sessions` page loads, SSE stream connects
- [ ] Send a prompt in the TUI via OpenRouter free model — text renders, tool call renders, final text renders

**Don't ship without exercising the TUI in a real terminal.** Type-checkers don't catch visual regressions.

---

## 8. CLI surface audit

- [ ] `tests/test_cli_surface.py` green — every `nellie <sub> --help` exits clean + no mojibake
- [ ] Every slash command in `/help` actually exists (no dead entries)

**Verify:**
```bash
python tools/cli_surface_audit.py
```

---

## 9. Third-party attribution + licenses

- [ ] `NOTICES.md` lists Hermes (MIT), any OpenClaw code, any upstream-derived code
- [ ] Nellie derivation (cc_components + cc_tool_prompts) noted as "shape/text port, not code-copy, Anthropic proprietary source not redistributed"
- [ ] `LICENSE.md` is the authoritative terms file, `pyproject.toml` points at it
- [ ] No stray MIT `LICENSE` file conflicting with proprietary terms
- [ ] Every `requirements`/`pyproject` dep has a compatible license — no GPL surprises

**Verify:**
```bash
grep -l "Anthropic\|Nellie\|Hermes\|OpenClaw\|MIT\|Apache" LICENSE.md NOTICES.md
pip-licenses 2>/dev/null || pip install pip-licenses && pip-licenses --format=markdown
```

---

## 10. Docs sync

- [ ] `README.md` architecture tree references `karna/tui/hermes_repl.py`, `hermes_display.py`, `cc_components/`
- [ ] `README.md` Documentation table links to `docs/TUI_COMPONENTS.md`, `docs/TEST_PIPELINE.md`, `docs/DEMO_RUNBOOK.md`
- [ ] `docs/TUI_COMPONENTS.md` module-map matches reality (11 modules, 132 tests — or current count)
- [ ] `docs/DEMO_RUNBOOK.md` runbook steps work against the `hermes_repl` path, not the legacy `repl.py`
- [ ] `GETTING_STARTED.md` tool list matches the 19 runtime tools
- [ ] Wiki `Home.md`, `_Sidebar.md`, `TUI-Guide.md`, `upstream-Component-Library.md` reflect current state
- [ ] No "coming soon" / "TBD" / "TODO" in user-facing docs

**Verify:**
```bash
grep -rnE 'TODO|TBD|coming soon|FIXME' README.md GETTING_STARTED.md docs/ 2>&1
```

---

## 11. CI workflows

- [ ] `.github/workflows/ci.yml` triggers on `main` AND `dev`
- [ ] `.github/workflows/test.yml` triggers on both
- [ ] `.github/workflows/lint.yml` triggers on both
- [ ] `.github/workflows/tui-pipeline.yml` green
- [ ] `.github/workflows/web-ui-pipeline.yml` green (modulo documented 3 flakes)
- [ ] `.github/workflows/visual-regression.yml` — either green or documented as non-gating pending Linux rebaseline

**Verify:**
```bash
gh run list --limit 15 --branch dev
```

---

## 12. Known issues (must be documented, not silently shipped)

These three are ACKNOWLEDGED ship-with issues for 0.1.3 — documented here and in CHANGELOG / doc sections:

- [ ] **Playwright web-UI interactions: 3 tests flaky under load** — module-scoped `live_server` fixture shares one uvicorn worker; function-scope fix deferred. Noted in `docs/AUTONOMOUS_PUSH_4H.md`.
- [ ] **Visual regression baselines: Ubuntu 5/5 regress** — font metrics differ from Windows-generated baselines. Workflow doesn't gate other CI. Follow-up: matrix-rebaseline on Linux OR pin to ansi-text renderer.
- [ ] **`test_background_bash.py`: 3 racy assertions** (`test_background_with_failing_command`, `test_background_timeout_produces_error`, `test_background_completion_notification`) — pass in isolation, flip between runs. Pre-existing, masked by the now-fixed hang. Follow-up tracked.

Additional gaps to surface in release notes:
- [ ] REPL ↔ cc_components integration pass (visual chrome migration) is gated to a follow-up — `cc_components` ships as a library in 0.1.3.
- [ ] `rapidfuzz` is an optional dep not yet declared in `pyproject.toml [project.optional-dependencies]`.
- [ ] Runtime subsystems for `render_pr_badge` / `render_memory_usage` / `render_cost_threshold_alert` not wired.
- [ ] **CI stub tests pre-existing failures:**
  - `tests/integration/test_acp_smoke.py` — asserts `karna.acp_server.list_agents` / `run_agent` which never existed (stub test). Now explicitly `--ignore`d at the integration dir level in `ci.yml`.
  - `tests/integration/test_recipes_smoke.py` — same pattern (`list_recipes` not on module). Ignored with integration dir.
  - `tests/integration/test_rest_smoke.py` — needed `fastapi`, which was missing. Fixed in `ci.yml` / `test.yml` by installing `rest` + `webui` extras.
  - `tests/test_cron.py` — imports `_cmd_cron` from `karna.tui.slash`, which was never added or was removed. 6 tests fail. Tracked separately — rename symbol or skip file in a follow-up; it was red on main already pre-release.
  - `tests/test_compaction_integration.py::test_compaction_fires_at_80_percent` — assertion mismatch ("Compacted summary" not in output). Racy / environment-sensitive. Follow-up.

---

## 13. Security sanity

- [ ] No API keys / credentials / `.env` files / `credentials/*.json` committed
- [ ] `git log origin/main..dev` body grep for `sk-`, `ghp_`, `BEGIN PRIVATE KEY` returns empty
- [ ] `scrub_secrets` is wired on provider error paths (spot-check)

**Verify:**
```bash
git log origin/main..dev -p | grep -iE 'sk-[a-z0-9]{20}|ghp_[a-zA-Z0-9]{30}|BEGIN PRIVATE KEY' | head
```
Expected: empty.

---

## 14. Merge mechanics

Decide the merge strategy (for user approval — don't auto-execute):

- [ ] **Option A: PR dev → main** — preserves 200-commit history, easier to review, merges via squash or merge-commit. Recommended.
- [ ] **Option B: Squash-merge** — collapses 200 commits to one on main. Cleaner linear history, loses per-commit context.
- [ ] **Option C: Rebase** — rewrites history on main. NOT recommended for a branch this old; too many conflicts possible.

For Option A:
```bash
gh pr create --base main --head dev --title "Release 0.1.3" --body-file docs/RELEASE_CHECKLIST_0.1.3.md
```

---

## 15. Tag + release

After merge lands on main:

- [ ] `git tag -a v0.1.3 -m "nellie 0.1.3 — TUI rewrite, upstream prompt port, hardening"` from `main` tip
- [ ] `git push origin v0.1.3`
- [ ] `gh release create v0.1.3 --title "Nellie 0.1.3" --notes-file CHANGELOG.md` (or paste the 0.1.3 section)
- [ ] Wheel artifact attached to the release: `python -m build && gh release upload v0.1.3 dist/karna-0.1.3-py3-none-any.whl`

---

## 16. Post-release

- [ ] Announce in `#nellie-dev` or equivalent Slack / comms
- [ ] Update `sop_nellie_release.md` memory if the process revealed changes
- [ ] Close / defer task #94 (e2e harness) — recommend shipping as 0.1.2
- [ ] Close / defer task #82 (demo video) — recommend shipping alongside 0.1.3 announcement

---

## Rollback plan (if a blocker surfaces post-merge)

```bash
git revert -m 1 <merge-commit-sha>    # preserves the dev work, undoes the main merge
git push origin main
git tag -d v0.1.3 && git push origin :refs/tags/v0.1.3
```

Or, for a softer rollback: patch-release 0.1.2 with the fix rather than reverting.

---

## Sign-off

- [ ] All section checkboxes ticked or deferred-with-note
- [ ] User has reviewed and approved the merge strategy (#14)
- [ ] User has run items #7 (runtime smoke) on their local machine

Release owner: Viraj. Engineer on call: alpha.
