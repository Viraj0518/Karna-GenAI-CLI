# Getting Started with Nellie
**From: Viraj | For: Karna Analytics Team**

---

## What is Nellie?

Nellie is our internal AI coding assistant. It runs in your terminal, reads your codebase, writes code, runs commands, and learns your preferences. Think of it as having a senior engineer pair-programming with you 24/7 — except it costs pennies per hour.

It's built in-house, runs locally, sends zero telemetry, and works with any LLM provider.

---

## Setup (5 minutes)

### 1. Install

```bash
# Clone the repo (if you haven't)
git clone https://github.com/Viraj0518/Karna-GenAI-CLI.git
cd Karna-GenAI-CLI

# Install
pip install -e .

# Recommended — accurate token counting
pip install -e ".[tokens]"

# Optional — web search capabilities
pip install -e ".[web]"
```

Verify: `nellie --version` should print `0.1.3`.

### 2. Get an OpenRouter API key

**Why OpenRouter?** One API key gives you access to 200+ models — Claude, GPT, Llama, Qwen, DeepSeek, and dozens of free ones. No need for separate accounts everywhere.

1. Go to [openrouter.ai](https://openrouter.ai) → sign up (GitHub login works)
2. **Add $5 to your account** — this lasts weeks with cheap models, and you can test with free models at $0 cost
3. Go to [openrouter.ai/keys](https://openrouter.ai/keys) → Create Key
4. Copy the key (starts with `sk-or-v1-...`)

### 3. Configure

```bash
# Add your key to your shell (one-time setup)
echo 'export OPENROUTER_API_KEY="sk-or-v1-paste-your-key-here"' >> ~/.bashrc
source ~/.bashrc

# Set your default model (start free — best free coding model available)
nellie model set openrouter:qwen/qwen3-coder
```

### 4. Run

```bash
cd your-project/
nellie
```

You're in. Start asking questions, requesting code changes, or exploring the codebase.

---

## Which Model Should I Use?

### Free models — $0 (no credit card needed)

These are fully free on OpenRouter. Rate-limited to 20 req/min and 200 req/day, but plenty for normal use.

| Model | Context | Best for | Set with |
|---|---|---|---|
| **Qwen3 Coder 480B** ⭐ | 262K | **Best free coding model.** Purpose-built for agentic coding, tool-use, repo-scale context. Start here. | `openrouter:qwen/qwen3-coder` |
| **Qwen3 Next 80B** | 262K | General reasoning + tool-use, strong all-rounder for non-coding questions | `openrouter:qwen/qwen3-next-80b` |
| **GPT-OSS 120B** | 131K | OpenAI's first open-weight model (Apache 2.0), solid coder | `openrouter:openai/gpt-oss-120b` |
| **GPT-OSS 20B** | 131K | Lighter GPT-OSS, faster responses for quick tasks | `openrouter:openai/gpt-oss-20b` |
| **Nemotron-3 Super 120B** | 262K | NVIDIA's hybrid architecture, great for analyzing large files | `openrouter:nvidia/nemotron-3-super-120b` |
| **Gemma 4 27B** | 262K | Google's latest, supports images + tools | `openrouter:google/gemma-4-27b-it:free` |
| **DeepSeek R1** | 164K | Deep reasoning, chain-of-thought problem solving | `openrouter:deepseek/deepseek-r1:free` |
| **Llama 3.3 70B** | 66K | Proven workhorse, GPT-4 level quality | `openrouter:meta-llama/llama-3.3-70b-instruct` |

**My recommendation:** Start with **Qwen3 Coder** — it's the strongest free model for coding right now, built specifically for agentic tool-use (which is exactly how Nellie works). When you hit the daily rate limit, switch to **GPT-OSS 120B** or **Llama 3.3 70B** for the rest of the day.

### Cheap models — pennies per session (fund $5 to unlock)

Once you're comfortable, these give better quality for almost nothing:

| Model | ~Cost/session | Best for |
|---|---|---|
| **DeepSeek Chat V3** | $0.01–0.05 | Strong coder, very cheap — **best value per dollar** |
| **GPT-4o Mini** | $0.01–0.03 | Fast, good for simple tasks |
| **Claude Haiku 4.5** | $0.02–0.08 | Fast Anthropic, good reasoning |
| **MiniMax M2.7** | $0.01–0.05 | Huge context window, great for analyzing big files/datasets |

### Premium models — when you need the best

For architecture decisions, complex debugging, or critical production code:

| Model | ~Cost/session | When to use |
|---|---|---|
| **Claude Sonnet 4** | $0.10–0.50 | Complex reasoning, production-grade code |
| **Claude Opus 4** | $0.50–2.00 | Hardest problems only |
| **GPT-4o** | $0.05–0.20 | Good all-rounder |

**Bottom line:** Use **Qwen3 Coder (free)** for the first couple weeks. Switch to **DeepSeek V3** as your daily driver when you want consistently better output. Use **Claude Sonnet** when you hit something genuinely hard. Your $5 will last a month.

Switch models mid-conversation: `/model openrouter:deepseek/deepseek-chat-v3`

Check your spend anytime: `/cost`

---

## Best Practices for the Analytics Team

### 1. Set up your project first

```bash
cd your-analytics-repo/
nellie init
```

This creates a `KARNA.md` file. **Edit it** — this is how Nellie learns your project:

```markdown
# KARNA.md

## About
Internal analytics platform. Python 3.11 + Pandas + Polars + DuckDB.

## Conventions
- Data pipelines in pipelines/
- Dashboards in dash/
- Tests mirror source structure in tests/
- Use polars over pandas for new code
- SQL queries go in queries/ as .sql files, not inline strings
- Column names: snake_case, never camelCase

## Important
- Never modify production configs in config/prod/
- Raw data in data/ is gitignored — never commit CSVs
- Use DuckDB for ad-hoc analysis, PostgreSQL for production
```

The better this file, the better Nellie's suggestions.

### 2. Great prompts for analytics work

**Data exploration:**
```
Read the schema at queries/create_tables.sql and explain what 
the user_events table tracks. Then write a polars query that 
shows daily active users for the last 30 days.
```

**Pipeline debugging:**
```
The pipeline at pipelines/etl_daily.py is failing with a 
KeyError on 'timestamp_utc'. Read the file and the input 
schema at schemas/events.json — find the mismatch.
```

**Dashboard help:**
```
Read dash/revenue.py and add a new chart showing MoM revenue 
growth as a line chart. Use the existing db connection pattern.
```

**SQL optimization:**
```
Read queries/slow_query.sql — it takes 45 seconds on 10M rows. 
Suggest index changes and query rewrites to get it under 5 seconds.
```

### 3. Let Nellie read before writing

Always ask Nellie to **read and understand** before making changes:
```
Read the entire pipelines/ directory structure and explain 
how data flows from ingestion to the dashboard.
```

Then ask for changes. Context = quality.

### 4. Use `/compact` when conversations get long

After 30+ back-and-forth messages, Nellie's context window fills up. Type `/compact` to summarize older messages and free space — you won't lose the important context.

### 5. Resume past sessions

```bash
# See recent sessions
nellie history search "that duckdb query"

# Resume where you left off
nellie resume
```

Every conversation is saved with full-text search.

---

## Available Tools

Nellie has 19 built-in tools it uses automatically:

| Tool | What it does |
|---|---|
| `bash` | Run shell commands (python scripts, pip install, etc.) |
| `read` | Read files from your project |
| `write` | Create or overwrite files |
| `edit` | Find-and-replace within files |
| `grep` | Search file contents with regex |
| `glob` | Find files by pattern (`**/*.py`) |
| `git` | Git operations (status, diff, commit, branch) |
| `web_search` | Search the web for docs/solutions |
| `web_fetch` | Fetch and extract web page content |
| `notebook` | Read and edit Jupyter notebooks (execution requires `jupyter nbconvert` or `papermill` on PATH) |
| `monitor` | Watch background processes (long-running scripts) |
| `task` | Track tasks within a session |
| `clipboard` | Read/write system clipboard |
| `image` | View images (with multimodal models) |
| `mcp` | Connect to MCP servers for extended capabilities |
| `browser` | Headless Chromium via Playwright (navigate, click, fill, screenshot) — optional `[browser]` extra |
| `db` | Query SQLite / PostgreSQL / MySQL with read-only default + parameter binding |
| `comms` | Inter-agent messaging via the file-based inbox (`send` / `check` / `read` / `reply`) |
| `document` | Extract text and tables from PDF, Office, CSV, and HTML files |

When Nellie needs multiple reads/greps at once, they run in parallel — fast.

---

## Slash Commands

Type these during a conversation:

| Command | What it does |
|---|---|
| `/help` | Show all commands |
| `/model <provider:model>` | Switch to a different model |
| `/cost` | Check session and total spend |
| `/compact` | Free up context space (auto-triggers at 80% window) |
| `/history` | Show conversation so far |
| `/sessions` | List recent sessions |
| `/resume <id>` | Continue a past session |
| `/tools` | See available tools |
| `/skills [enable\|disable <name>]` | List, enable, or disable skills |
| `/memory [search\|show\|forget]` | View, search, or manage memories |
| `/loop <goal>` | Repeat-until-done autonomous agent |
| `/plan <goal>` | Think first, read-only plan mode |
| `/do` | Execute the last plan from `/plan` |
| `/copy` | Copy last response to clipboard |
| `/paste` | Paste clipboard into prompt |
| `/clear` | Fresh conversation, same session |
| `/exit` | End session |

---

## Skills

Skills are reusable workflows. Nellie loads them from `.karna/skills/` in your project or `~/.config/karna/skills/` globally. Skills are injected into the system prompt when triggered -- they guide the model's behavior for specific tasks.

### Managing skills

```bash
# Inside the REPL:
/skills                    # List all loaded skills with status
/skills enable sql-review  # Enable a disabled skill
/skills disable sql-review # Disable without deleting
```

### Creating a skill

Create `.karna/skills/sql-review.md`:
```markdown
---
name: sql-review
description: Review a SQL query for performance and correctness
triggers: ["/sql-review", "review this sql", "optimize query"]
---

Read the SQL file the user specifies. Check for:
1. Missing indexes on WHERE/JOIN columns
2. SELECT * (suggest specific columns)
3. N+1 query patterns
4. Implicit type casting
5. Missing LIMIT on exploratory queries

Suggest concrete improvements with the rewritten query.
```

Then type `/sql-review queries/slow_report.sql` in Nellie and it runs the workflow. You can also trigger skills with keyword phrases like "review this sql".

### Skill file format

| Frontmatter field | Required | Description |
|---|---|---|
| `name` | Yes | Unique skill identifier |
| `description` | Yes | Short description (shown in `/skills` list) |
| `triggers` | Yes | List of slash commands or keyword phrases |
| `enabled` | No | `true` (default) or `false` |
| `version` | No | Skill version string |
| `author` | No | Author name |

The body (below the `---` frontmatter) contains the full instructions injected into the system prompt when the skill is triggered.

---

## Memory System

Nellie automatically learns your preferences, project conventions, and corrections across sessions. Memories persist in `~/.karna/memory/` as markdown files with YAML frontmatter.

### How auto-memory works

Every message you send is scanned for patterns (regex-based, zero LLM cost):

| Pattern type | Example triggers | Memory type |
|---|---|---|
| **Corrections** | "don't use pandas", "that's wrong", "never add emojis" | `feedback` |
| **Confirmations** | "yes, exactly like that", "keep doing it that way" | `feedback` |
| **Self-identification** | "I'm a data engineer", "I prefer vim keybindings" | `user` |
| **Project facts** | "we deploy to AWS", "our convention is snake_case" | `project` |
| **References** | URLs, "bugs are tracked in Linear", "docs at..." | `reference` |

Memories are deduplicated against existing entries and rate-limited (min 5 turns between saves) to avoid noise.

### Managing memories

```bash
# Inside the REPL:
/memory                    # List all memories in a table
/memory search <query>     # Full-text search across memories
/memory show <name>        # Display full content of a memory
/memory forget <name>      # Delete a memory
```

### Memory file format

```markdown
---
name: Memory title
description: One-line description
type: user|feedback|project|reference
---

Memory content here.
```

---

## KARNA.md -- Project Instructions

`KARNA.md` is the most important file for getting good results from Nellie. It teaches the agent your project's stack, conventions, and boundaries.

### Setup

```bash
cd your-project/
nellie init              # Auto-detects stack, generates KARNA.md
nellie init --minimal    # Minimal starter template
```

### Hierarchy

Nellie loads instruction files in priority order and merges them:

```
1. {project_root}/KARNA.md          -- highest priority (team conventions)
2. {project_root}/.karna/KARNA.md   -- alternate location
3. ~/.karna/KARNA.md                -- global personal preferences
4. CLAUDE.md                        -- Claude Code compatibility
5. .cursorrules                     -- Cursor compatibility
6. .github/copilot-instructions.md  -- Copilot compatibility
```

This means you can have global preferences in `~/.karna/KARNA.md` (like "always use vim keybindings") and project-specific rules in your repo's `KARNA.md`. Both are loaded -- project-level wins on conflicts.

### What to put in KARNA.md

```markdown
# KARNA.md

## Stack
Python 3.11, Polars, DuckDB, FastAPI

## Conventions
- Use polars over pandas for new code
- SQL queries in queries/ as .sql files
- Tests mirror source structure
- Commit messages: conventional commits format

## Don't touch
- config/prod/ (production secrets)
- migrations/ (use alembic)

## Agent defaults
- Always run tests after modifying code
- Read existing patterns before writing new code
```

---

## Security

- **Zero telemetry** — nothing leaves your machine except API calls to your chosen LLM provider
- **API keys** stay in your shell environment, never in the repo
- **Secret scrubbing** — if you accidentally paste a key in conversation, Nellie redacts it
- **Path guard** — can't access files outside your project directory
- **Dangerous command warnings** — warns before `rm -rf`, `DROP TABLE`, etc.
- **Permission system** — configure ALLOW/ASK/DENY per tool in `.karna/permissions.toml`

---

## Cost Control

| Action | How |
|---|---|
| Check spend | `/cost` in session or `nellie cost` from terminal |
| Set budget alert | Edit `~/.config/karna/config.toml` → `cost_threshold = 1.0` |
| Use free models | `/model openrouter:qwen/qwen3-coder` |
| Reduce token usage | `/compact` to summarize old messages |

With DeepSeek V3 as daily driver and $5 funded: expect **4–6 weeks of daily use** before needing to top up.

---

## Quick Reference Card

```
Install:    pip install -e .
Run:        cd your-project/ && nellie
Setup:      nellie init
Model:      nellie model set openrouter:qwen/qwen3-coder
Help:       /help
Cost:       /cost
Switch:     /model openrouter:deepseek/deepseek-chat-v3
Compact:    /compact
Search:     nellie history search "keyword"
Resume:     nellie resume
```

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `nellie: command not found` | `pip install -e .` then add `~/.local/bin` to your PATH |
| `No API key found` | `export OPENROUTER_API_KEY=sk-or-v1-...` |
| `Model not found` | Browse models at [openrouter.ai/models](https://openrouter.ai/models) |
| `Rate limited` | Wait 60s, or switch models with `/model` |
| `Context too long` | Type `/compact` |
| Nellie doing something wrong | Be more specific in your prompt, or add rules to `KARNA.md` |

---

**Questions?** Ping me on Slack or check `docs/DEVELOPER_GUIDE.md` for the full technical deep-dive.

**Remember:** Start with Qwen3 Coder (free), get comfortable, then upgrade. The $5 is just so you have access to the good stuff when you need it.

— Viraj
