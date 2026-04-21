# Nellie — 5-Minute Demo Walkthrough

A step-by-step terminal script for a real-terminal recording of the
`dev` branch. The goal is to show the core agent loop + the new TUI
interactions (scroll, Esc-interrupt, queue) + one of the new tools
landing for production-demo.

**Setup once, before recording:**

```bash
git fetch && git checkout claude/alpha-nellie-tui-fixes-20260420
pip install -e ".[dev]"
export OPENROUTER_API_KEY="$(cat ~/.karna/credentials/openrouter.token.json | jq -r .token)"
# Pick a demo project directory; the one below is a sample Python app
cd ~/projects/sample-python-app
```

**Recording tool:** `asciinema rec demo.cast --idle-time-limit 2 --title "Nellie v0.2 demo"` (or OBS with an xterm 120x32 capture).

---

## 0:00–0:15 — Start

Terminal should be clean. Run:

```bash
nellie
```

**Narrate (VO):** "Nellie — Karna's agent for the terminal. No SaaS,
no telemetry, connects to any model."

The banner renders. Status bar shows `openrouter/qwen/qwen3-coder` plus
an animated kawaii face. Leave it idle for 2s so the viewer sees the
UI settle.

---

## 0:15–0:45 — First turn: repo understanding

Type slowly (or paste):

```
what does this project do — summarize in 3 bullets
```

Press **Enter**. The status bar shifts to `reasoning…` then `writing…`.
Streaming text fills the output pane. **Don't narrate** during stream —
the TUI is the demo.

When it finishes, the status bar returns to idle. Viewer sees the
per-turn cost footer: `[1,243 tokens, $0.0008]`.

**Narrate:** "Every turn shows token count and cost. No surprise bills."

---

## 0:45–1:30 — Tool call: grep + edit

Paste:

```
find the function that handles login and add a one-line docstring saying
"Authenticates the user against the identity provider."
```

What should happen, on screen:
1. `├─ 🔍 grep  query="login"  ✓ 0.3s` (grep tool)
2. `├─ 📖 read  src/auth.py  ✓ 0.1s` (read tool — may run in parallel with grep)
3. `├─ ✏️  edit  src/auth.py  ✓ 0.2s` (edit tool, followed by inline diff)
4. Final assistant text: "Added a docstring to `authenticate_user()`
   in `src/auth.py`."

**Narrate:** "Nellie runs read and grep in parallel, then serializes
the write. You see each step with timing and an inline diff."

---

## 1:30–2:15 — TUI interactions: scrollbar + Esc-interrupt

This is the new-on-dev demo. Scroll back through the previous output:

- Press **PgUp twice** — output pane scrolls up one page each press. The
  scrollbar on the right moves up. The input line at the bottom stays
  focused: you can keep typing.
- Press **End** — jumps back to the bottom; autoscroll re-engages.

Then send a deliberately long prompt that triggers a slow tool call:

```
summarize every python file in src/ one-by-one and then write a
combined architecture document
```

Wait ~2 seconds into the stream, then press **Esc**.

Expected: a dim line appears — `Esc — stopping at next checkpoint.
Ctrl-C to force-cancel.` The agent finishes its current event and
exits cleanly. Status bar returns to idle.

**Narrate:** "Esc asks the agent to stop at the next safe point. Ctrl-C
if you need a hard cancel. And if you need to steer while it's
running, just type — the message queues and shows up in the status bar
as `✉ 1 queued`, then injects mid-stream at the next event boundary."

---

## 2:15–3:30 — New tool: `db`

This is a new-on-dev feature. Have a sample sqlite file ready:
`demo_data.db` with a `users` table seeded via:

```sql
CREATE TABLE users(id INTEGER, name TEXT, role TEXT);
INSERT INTO users VALUES (1, 'alice', 'admin'), (2, 'bob', 'editor'),
                         (3, 'carol', 'viewer');
```

Then in Nellie:

```
connect to demo_data.db and tell me who the admins are
```

Expected:
1. `├─ 🗄  db  connect=demo_data.db  ✓ 0.1s`
2. `├─ 🗄  db  query=SELECT name FROM users WHERE role=?  ✓ 0.1s`
3. Markdown table rendered in the output pane with one row: `alice`.

**Narrate:** "Parameterised queries are the default — the agent binds
values through `params`, never string-interpolates. Read-only by
default too; mutations need an explicit flag."

---

## 3:30–4:15 — Skills + KARNA.md

```
/skills
```

Shows the enabled/disabled skill list. Viewer sees things like
`docstrings`, `code-review`, `summarize`.

Then:

```
enable the code-review skill
```

Status bar shows skill matched. Then:

```
review src/auth.py for anything that looks off
```

Nellie uses the skill's instructions to shape its review. **Narrate:**
"Skills are markdown files in `~/.karna/skills`. Turn them on, turn
them off, match on keywords — they reshape the agent's behaviour for
that turn without cluttering the base system prompt."

---

## 4:15–4:45 — Memory + session persistence

```
/memory list
```

Shows the auto-extracted memories from this session
(project name, conventions noted, corrections made).

```
/history
```

Short scrollable history of the turns so far.

```
/cost
```

Session total: tokens, prompts, completions, dollars. **Narrate:**
"State persists in sqlite — resume any past session with
`nellie resume`."

---

## 4:45–5:00 — Exit

```
Ctrl-D
```

Clean `Goodbye.` line. Terminal returns to the shell.

**Narrate:** "That's Nellie. Ships zero telemetry, runs on any model,
and the whole agent loop is ~5k lines of Python. The dev branch is
at github.com/Viraj0518/Karna-GenAI-CLI."

---

## Retake checklist

If you have to re-record a segment, these are the non-obvious things
that ruin takes:

- **Terminal width must be ≥120 cols** — narrower and the scrollbar
  eats the timestamps column.
- **Type at ~8 chars/sec** — real enough to read, fast enough to stay
  under 5 min total.
- **Don't interrupt the first turn.** The viewer needs one full clean
  turn to orient on the layout before you start showing interrupts.
- **Run the demo once before recording** to prime Hugging Face model
  downloads (sentence-transformers etc.) — otherwise first-turn RAG
  context stalls for 30s while it downloads.
- **If you fluff a line, pause 2s in silence, then resume** —
  post-editing can cut the dead air.

## Post-processing

1. Trim leading/trailing idle with ffmpeg: `ffmpeg -i raw.mp4 -ss
   00:00:02 -to 00:05:00 -c copy trimmed.mp4`.
2. Overlay VO with ducking (sidechain compression so terminal typing
   doesn't clash) — Audacity or Adobe Premiere.
3. Export 1080p 30fps, H.264 MP4, AAC audio. Upload to the Karna
   internal SharePoint + the public landing page if approved.
