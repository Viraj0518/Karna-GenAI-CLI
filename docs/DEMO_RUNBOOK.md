# Nellie Demo Runbook

Copy-paste sequence for a clean live demo. Every step has a **what** and a **why** so you can narrate confidently. All six surfaces in one flow.

---

## 0. Pre-flight (do this BEFORE the demo)

```
cd C:\Users\12066\Karna-GenAI-CLI
git checkout dev
git pull
pip install -e '.[all]'            # or just '.[rest,recipes]' for the minimal demo
python -m pytest tests/ -q --ignore=tests/test_live_api.py --ignore=tests/integration -x
```

Expect ~140 tests pass in ~90s. If anything's red, stop — fix first.

**Optional**: set a screen recorder. On Windows, `Win+Alt+R` opens Xbox Game Bar. Or:

```
python tools/record_demo.py capture 600 --tag live-demo
```

---

## 1. TUI (the main event, 2 min)

```
nellie
```

Narrate: NELLIE banner renders, `openrouter/<model>` in status bar, "19 loaded tools" + a charm, version v0.1.0.

Send three prompts in order:

| Prompt | Narration |
|---|---|
| `hey!` | "Greeting. Watch the `✦ Thinking…` spinner appear the instant I press Enter, then the `◆ nellie` label streams in." |
| `plan a refactor of our auth middleware` | "Structured planning. Headers, nested bullets, numbered steps — rendered live as tokens arrive." |
| `read karna/tui/output.py and summarise in one sentence` | "Tool use. Watch the `● Read(karna/tui/output.py)` bullet, then `  ⎿  962 lines  ✓ 0.3s` result branch. Same glyph vocabulary as Claude Code." |

Bottom status bar: live `⠙ Thinking · 4s · ↑ 2.1k tok · esc` counter during each turn.

Exit with `/exit`.

---

## 2. Model switching (30 sec)

```
nellie
/model openrouter:anthropic/claude-opus-4
```

Narrate: "Every provider's `max_tokens` now inherits from the canonical 1,359-model registry. Opus-4 caps at 128K output; haiku caps at 8K. Nothing silently truncates. This is the OpenClaw-pattern resolver across 7 providers."

---

## 3. REST server (1 min, optional terminal split)

Terminal 2:
```
nellie serve
```

Terminal 3:
```
curl http://127.0.0.1:3030/health
curl -X POST http://127.0.0.1:3030/v1/sessions -H 'Content-Type: application/json' -d '{}'
```

Narrate: "FastAPI + SSE + WebSockets. 10 endpoints. OpenAPI auto-gen. Same agent loop as the TUI."

---

## 4. Web UI (1 min)

```
nellie web
```

Browser opens `http://127.0.0.1:3030`. Click through:
- **Sessions** → see the landing session from step 1
- Click into a session → live transcript streams via SSE (the fix landed in `dfc2102` — subscriptions wired, tokens appear in real time)
- **Recipes** → Recipe Library with install hint
- **Memory** → memory viewer with create/edit/delete

Narrate: "Gamma's FastAPI + Jinja2 + htmx UI. 500 lines of CSS. Mobile-responsive. Electron desktop wraps this exact UI."

---

## 5. Electron (30 sec, if packaged)

```
cd electron
npm start
```

BrowserWindow opens with the same web UI but native chrome. Narrate: "Electron shell spawns `nellie web` as a child, polls `/health`, loads the URL. Graceful SIGTERM shutdown. Single-instance lock. `electron-builder` → dmg/msi/AppImage."

---

## 6. Agent-to-agent (30 sec, advanced)

```
nellie mcp serve                    # in another terminal, or via Claude Desktop config
nellie acp serve                    # same but ACP protocol
nellie mcp serve-memory             # persistent memory MCP
```

Narrate: "Nellie as a subagent. Claude Desktop, another Nellie, or any MCP/ACP client can drive it over stdio."

---

## 7. Recipes (30 sec)

```
cat > /tmp/greet.yaml <<'EOF'
title: Greet
description: Toy recipe
parameters:
  - key: name
    input_type: string
    requirement: required
prompt: "Say hi to {{name}} in one sentence."
EOF

nellie run --recipe /tmp/greet.yaml --values name=Audience
```

Narrate: "YAML + Jinja2. Sub-recipes compose — gamma's engine supports 3-level nesting."

---

## 8. Close

```
python tools/stress_100.py openrouter:openai/gpt-oss-120b:free
```

Narrate (while it runs): "100-turn real-provider stress. Persistent conversation, memory, rate-limit handling. About 6 minutes end-to-end. We run this on every PR to main."

Expected: `98 ok · 0 empty · 2 rate-limited-then-retried · ~340s total`.

---

## What to do if something fails live

| Symptom | Fallback |
|---|---|
| TUI shows blank pane for 2+ seconds | `/model openrouter:anthropic/claude-haiku-4.5` — faster route |
| Provider 429s | Wait 10s, retry. Free-tier rate limits are aggressive. |
| Web UI won't open | `nellie web --port 3031` (port 3030 may be occupied) |
| Electron won't launch | `cd electron && npm install && npm run dev` — `dev` mode shows logs |
| MCP/ACP server exits immediately | `nellie mcp serve 2>&1 \| head` — check for missing provider creds |

## Demo-risk flags (from beta's CC-testing)

- **Wayland**: pyautogui (computer_controller MCP) fails on pure Wayland. Demo on X11/Windows/macOS only.
- **macOS a11y**: first keyboard/mouse call triggers permission prompt. Answer it pre-demo.
- **FAILSAFE is OFF**: cursor-corner-kills is disabled. Call out in patter if relevant.
- **HiDPI**: pyautogui uses logical pixels. Don't trust raw screenshot coords.

## One-liner cheat sheet

```
nellie                              # TUI
nellie serve                        # REST :3030
nellie web                          # Web UI :3030 + open browser
nellie acp serve                    # ACP over stdio
nellie mcp serve                    # Nellie-as-subagent
nellie mcp serve-memory             # Memory MCP
nellie run --recipe <path.yaml>     # Execute a recipe
cd electron && npm start            # Desktop shell
python tools/stress_100.py          # 100-turn real-provider drive
python tools/cli_surface_audit.py   # Sanity-check every CLI subcommand
```
