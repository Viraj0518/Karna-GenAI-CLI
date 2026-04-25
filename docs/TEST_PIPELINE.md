# Nellie Test Pipeline

Automated coverage of every demo surface so regressions are caught in CI,
not by a user with a screen recording.

## Layers

| Layer | What it catches | Runtime |
|---|---|---|
| **Unit tests** (`tests/test_*.py`, excl. integration/visual) | logic bugs, module-level imports, protocol correctness | ~15s |
| **Headless TUI** (`tests/test_tui_integration.py`, `test_repl_imports.py`) | REPL queue-vs-fresh-turn contract, closure-scoped identifier misses, writer deque invariant | <1s |
| **Visual regression** (`tests/test_visual_regression.py`) | ANSI rendering drift for 5 canonical turn scenarios via rich-SVG→PNG pixel diff | 15–30s |
| **Web UI audit** (`tests/test_web_ui_visual.py`) | every web page at desktop + mobile viewports via Playwright, asserts key elements + zero console errors | 30–60s |
| **Provider contract** (`tests/test_max_tokens_resolver.py`) | OpenClaw-parity `max_tokens` resolver across 7 providers | <1s |
| **Demo surfaces** (`tests/test_{rest,acp,recipes,mcp,websocket,telemetry,electron_shell}_*.py`) | each of the 26 parity rows boots + protocol-roundtrips | ~10s |
| **100-turn stress** (`tools/stress_100.py`) | real-provider persistent conversation, memory, rate-limit handling | ~6min |

## Running locally

```bash
# Everything except live API
python -m pytest tests/ -q --ignore=tests/test_live_api.py --ignore=tests/integration

# TUI-only gate (fast regression catch)
python -m pytest tests/test_tui_integration.py tests/test_repl_imports.py tests/test_tui.py -v

# Web UI visual audit (launches real nellie web + chromium)
python -m pytest tests/test_web_ui_visual.py -v
# Screenshots land in _web_screenshots/ + REPORT.md with embedded images

# Visual TUI regression
python tools/visual_regression.py --mode=check
# Diff report in _visual_diff/REPORT.md

# Live-provider stress (needs OPENROUTER_API_KEY)
python tools/stress_100.py openrouter:openai/gpt-oss-120b:free
```

## CI workflows

| Workflow | Triggers | What it runs |
|---|---|---|
| `tui-pipeline.yml` | push/PR to main/dev | headless TUI + provider contract + demo surfaces + (on main PRs) 100-turn stress |
| `web-ui-pipeline.yml` | push/PR to main/dev | Playwright audit, artifact upload of 10 screenshots |
| `visual-regression.yml` | push/PR to main/dev | ANSI→PNG baseline diff, artifact upload on failure |
| `test.yml` | push/PR to main/dev | min + max Python × ubuntu + windows |
| `ci.yml` | push/PR to main/dev | ruff + mypy + pytest |
| `lint.yml` | push/PR | ruff check + format |

## Analysing a user-submitted video

```bash
python tools/record_demo.py analyse "<path-to.mp4>"
```

Extracts 1 fps frames, runs tesseract OCR on a 5-frame timeline, writes
`<stem>_frames/REPORT.md` with inline thumbnails + flags (e.g. "final
frame has a user prompt but no assistant marker → possible silent turn").
Used by alpha to diagnose user-caught TUI issues without a follow-up.

## Recording a demo

```bash
python tools/record_demo.py capture 30 --tag nellie-demo
```

ffmpeg gdigrab (Windows) / avfoundation (mac) / x11grab (Linux).
Falls back to instructing the user to hit `Win+Alt+R` (Xbox Game Bar)
if ffmpeg is missing.

## How this grew

Viraj hit a TUI bug on turn 2 of his first real run: type prompt 2 while
turn 1 is still streaming, get silently queued as a steering message,
pane looks blank. Three fix/diagnose cycles later it was clear the
guessing cost more than the infrastructure to catch it. This pipeline
is the permanent fix: every future blank-pane symptom fails here, not
in someone's screen recording.
