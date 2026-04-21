# Phase 4 Design Document -- Advanced Features

> **SUPERSEDED — 2026-04-20 [alpha].** This document is a historical design
> record. Most of the features it proposes have since shipped (often with
> different interfaces than the designs below). See
> [docs/CODEBASE_MAP.md](CODEBASE_MAP.md) for the current state and
> [docs/DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) for the architecture
> walkthrough. Mapping from the sections below to what actually landed:
>
> | Section | Current implementation |
> |---|---|
> | 1. Autonomous Loop Mode | [karna/agents/autonomous.py](../karna/agents/autonomous.py) + `/loop` slash command ([karna/tui/slash.py](../karna/tui/slash.py)) |
> | 2. Multi-Model Verification | Mixture-of-agents — see `tests/test_moa.py` and the MOA references in [karna/agents/](../karna/agents/) |
> | 3. Cost-Aware Auto-Routing | Not yet implemented; still the design of record |
> | 4. Fork / Replay | Session fork — see `tests/test_fork_session.py` |
> | 5. Collaborative Multi-Agent | [karna/agents/subagent.py](../karna/agents/subagent.py) + [karna/tools/task.py](../karna/tools/task.py) (FSM + worktree isolation); parallel dispatch in [karna/agents/parallel.py](../karna/agents/parallel.py) |
> | 6. Full Compaction with LLM Summarization | [karna/compaction/compactor.py](../karna/compaction/compactor.py) (threshold, circuit breaker, preserved tail) |
> | 7. Web Frontend (Next.js 15) | Not yet implemented; `web/` is the tracking directory |
> | 8. Gateway -- Telegram | Not yet implemented; still the design of record |
>
> Interface names and field shapes in the design below are **not** current —
> refer to the code. Sections 3, 7, and 8 are still unimplemented and can
> still be treated as the design of record for those features.

**Status:** Draft (historical)
**Author:** gamma (Claude)
**Date:** 2026-04-17
**Branch:** `claude/gamma-scaffold`

Phase 4 builds on the working agent loop (`karna/agents/loop.py`), provider
system, TUI, and tool infrastructure shipped in Phases 1--3.  Every feature
below is specified as a **design** -- interfaces, data flow, persistence, and
open questions -- not implementation.

Reference implementations studied:

- `cc-src/src/Task.ts` -- task lifecycle FSM (`pending -> running -> completed|failed|killed`)
- `cc-src/src/coordinator/coordinatorMode.ts` -- coordinator/worker split, subagent spawning
- `cc-src/src/services/compact/` -- auto-compaction triggers, LLM-summarized compaction, prompt caching
- `cc-src/src/services/autoDream/autoDream.ts` -- background forked agents with time-gates and lock files

---

## 1. Autonomous Loop Mode

### Concept

```
nellie loop "check if tests pass, fix failures, commit when green" --interval 5m
```

The agent runs a user-supplied prompt on a repeating timer.  Between
iterations it sleeps (or paces dynamically).  The loop is a first-class CLI
command, not a hack over the REPL -- it has its own persistence, crash
recovery, and lifecycle management.

### Architecture

```
                    LoopController
                   /      |       \
            Scheduler   AgentLoop   StateStore
           (asyncio)   (existing)  (~/.karna/loops/<id>/)
```

Each iteration is a full `agent_loop()` turn from `karna/agents/loop.py` with
the same tool access as an interactive session.  The scheduler wraps this in a
retry/sleep cycle.

### Interface

```python
class LoopState(BaseModel):
    loop_id: str
    prompt: str
    interval_seconds: int
    max_iterations: int | None       # None = unlimited
    current_iteration: int
    status: Literal["running", "paused", "stopped", "completed"]
    last_result: str | None          # summary of last iteration
    last_iteration_at: datetime | None
    created_at: datetime
    model: str
    provider: str

class LoopController:
    """Manages a single autonomous loop lifecycle."""

    async def start(
        self,
        prompt: str,
        interval: int = 300,
        max_iterations: int | None = None,
    ) -> str:
        """Start a new loop, return loop_id."""

    async def pause(self) -> None:
        """Pause between iterations (current iteration finishes)."""

    async def resume(self) -> None:
        """Resume a paused loop."""

    async def stop(self) -> None:
        """Stop permanently."""

    def get_state(self) -> LoopState:
        """Return current state (for TUI status bar)."""
```

### Persistence

State lives at `~/.karna/loops/<loop_id>/state.json`.  On every iteration
boundary the controller writes the full `LoopState`.  On startup, if an
existing state file has `status == "running"`, the controller resumes from
`current_iteration`.

Conversation history for the loop is stored in the same directory as
`conversation.jsonl` (one JSON object per message), so the loop has
full context across iterations.

### Iteration Flow

```
1. Read state from disk
2. Build conversation: system prompt + loop prompt + prior results summary
3. Run agent_loop() with full tool access
4. Extract result from final assistant message
5. Evaluate stop conditions:
   a. agent said "TASK_COMPLETE" -> status = completed
   b. current_iteration >= max_iterations -> status = completed
   c. Ctrl+C / SIGINT -> status = stopped
6. Write state to disk
7. Sleep for interval (dynamic: halve interval on failures, double on success, clamp to [30s, interval])
8. Goto 1
```

### CLI

```
nellie loop <prompt> [--interval 5m] [--max-iter 20] [--model ...]
nellie loop --list                     # show active loops
nellie loop --pause <loop_id>
nellie loop --resume <loop_id>
nellie loop --stop <loop_id>
nellie loop --status <loop_id>
```

### cc-src Pattern Reference

The `autoDream.ts` system uses a similar gate/lock/fork pattern:
time-gate -> session-gate -> acquire lock -> run forked agent -> release lock.
Our LoopController simplifies this by making the schedule explicit rather than
opportunistic, and by persisting state to JSON rather than using mtime-based
locks.

### Open Questions

1. **Context growth:** After N iterations the conversation grows unbounded.
   Should we auto-compact between iterations, or keep a sliding window of the
   last K iteration results?
2. **Concurrent loops:** Should we allow multiple loops running simultaneously?
   If so, how do we handle git conflicts between loops that both write files?
3. **Notification:** When a loop completes or fails while the user is in a
   separate interactive session, how should we notify them?  Desktop
   notification?  Inline message if the TUI is open?
4. **Budget:** Should loops have a per-loop cost budget that automatically
   stops the loop when exceeded?

---

## 2. Multi-Model Verification

### Concept

```
nellie verify "is this migration safe?"
```

Sends the same prompt to two or more models in parallel, then semantically
diffs the responses to surface agreements and disagreements.

### Architecture

```
                  MultiModelVerifier
                  /        |        \
          Provider A   Provider B   DiffEngine
         (parallel)   (parallel)   (post-hoc)
```

### Interface

```python
class VerificationResult(BaseModel):
    responses: dict[str, str]    # model_id -> full response text
    agreements: list[str]        # bullet-point claims both models agree on
    disagreements: list[str]     # bullet-point claims where models diverge
    confidence: float            # 0.0-1.0, derived from agreement ratio
    total_cost_usd: float | None
    latency_ms: dict[str, int]   # model_id -> wall-clock ms

class MultiModelVerifier:
    def __init__(self, models: list[str], providers: dict[str, BaseProvider]):
        ...

    async def verify(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        diff_model: str | None = None,  # model used to produce the semantic diff
    ) -> VerificationResult:
        ...
```

### Execution Flow

```
1. Parse model list from config or --models flag
2. Build identical message payloads for each model
3. Fire all requests in parallel via asyncio.gather()
4. Collect responses + usage
5. Send all responses to a "diff model" (default: cheapest available) with prompt:
   "Given these N responses to the same question, list:
    - AGREEMENTS: claims that all responses support
    - DISAGREEMENTS: claims where responses contradict each other
    Output JSON: {agreements: [...], disagreements: [...]}"
6. Parse diff response into VerificationResult
7. confidence = len(agreements) / (len(agreements) + len(disagreements))
```

### Configuration

In `~/.karna/config.toml`:

```toml
[verification]
models = ["anthropic/claude-sonnet-4-6", "openrouter/gpt-4.1"]
diff_model = "openrouter/gpt-4.1-nano"   # cheap model for the diff step
auto_verify_threshold = 0.5               # below this confidence, flag for human review
```

### Display

The TUI renders agreements in green, disagreements in red/yellow, with the
confidence score in the status bar.  Each model's full response is available
via tab-switching (like fork branches).

### Open Questions

1. **Semantic diff quality:** Using an LLM for the diff step introduces its own
   hallucination risk.  Should we offer a deterministic fallback (e.g.,
   sentence-level embedding similarity)?
2. **Tool-use verification:** If the prompt requires tool use, do both models
   get tool access?  Or do we verify only the final text output?
3. **Streaming:** Can we stream both model responses simultaneously in the TUI
   (split pane)?
4. **Caching:** If model A and B share a provider, can we share prompt cache?

---

## 3. Cost-Aware Auto-Routing

### Concept

Tag tasks by complexity and route to the cheapest model capable of handling
them.  Users set a session budget; the router tracks spend and downgrades
when approaching the limit.

### Architecture

```
     User prompt
         |
    ComplexityClassifier
         |
    +-----------+-----------+
    |           |           |
  Simple     Medium      Complex
  (nano)    (sonnet)     (opus)
    |           |           |
    +-----+-----+-----------+
          |
     BudgetTracker
          |
     Provider.stream()
```

### Complexity Classifier

Two-stage classifier:

**Stage 1 -- Rule-based (zero cost):**

```python
def classify_rules(prompt: str, conversation: Conversation) -> Tier | None:
    # Short questions with no code context -> SIMPLE
    if len(prompt) < 200 and not has_code_blocks(prompt):
        return Tier.SIMPLE

    # Explicit routing keywords
    if any(kw in prompt.lower() for kw in ["architecture", "design", "refactor"]):
        return Tier.COMPLEX

    # Code review with file context -> MEDIUM
    if has_code_blocks(prompt) or len(conversation.messages) > 10:
        return Tier.MEDIUM

    return None  # fall through to LLM classifier
```

**Stage 2 -- LLM classifier (used when rules are ambiguous):**

Send the prompt to the nano model with:
```
Classify this task: SIMPLE, MEDIUM, or COMPLEX.
SIMPLE: factual questions, short explanations, simple edits
MEDIUM: code review, debugging, multi-file changes
COMPLEX: architecture decisions, large refactors, security analysis
Reply with one word.
```

Cost of classification: ~$0.0001 per query (nano model, <100 tokens).

### Tier Mapping

```python
class Tier(Enum):
    SIMPLE = "simple"      # nano/haiku class: $0.10-0.80/M input
    MEDIUM = "medium"      # sonnet class: $2-3/M input
    COMPLEX = "complex"    # opus class: $15/M input

TIER_MODELS: dict[Tier, list[str]] = {
    Tier.SIMPLE: ["openrouter/gpt-4.1-nano", "anthropic/claude-3-5-haiku"],
    Tier.MEDIUM: ["anthropic/claude-sonnet-4-6", "openrouter/gpt-4.1"],
    Tier.COMPLEX: ["anthropic/claude-opus-4-6", "openrouter/o3"],
}
```

### Budget Tracker

```python
class BudgetTracker:
    budget_usd: float          # set via --budget or config
    spent_usd: float = 0.0
    tier_overrides: int = 0    # times we downgraded due to budget

    def remaining(self) -> float:
        return self.budget_usd - self.spent_usd

    def should_downgrade(self, estimated_cost: float) -> bool:
        return (self.spent_usd + estimated_cost) > self.budget_usd * 0.8

    def record(self, usage: Usage) -> None:
        if usage.cost_usd:
            self.spent_usd += usage.cost_usd
```

When `should_downgrade()` returns True, the router drops one tier
(COMPLEX -> MEDIUM -> SIMPLE).  The TUI shows a warning: "Budget 80%
consumed, routing to sonnet."

### Override

`/model force:opus` sets a session-level override that bypasses the router.
The budget tracker still records spend but does not intervene.

### Open Questions

1. **Classification accuracy:** How often does the rule-based classifier
   misroute?  Should we log classification decisions and build a training set
   for a fine-tuned classifier?
2. **Mid-conversation tier changes:** If the conversation starts simple but
   becomes complex, should the router upgrade mid-stream or wait for the next
   user turn?
3. **Per-tool routing:** Some tool results (e.g., large file reads) inflate
   context.  Should the router factor tool-result token count into tier
   decisions?
4. **Multi-turn coherence:** Switching models mid-conversation risks losing
   nuance.  Should we pin a model for the duration of a multi-turn task?

---

## 4. Fork / Replay

### Concept

Conversation branching -- try a different approach without losing the
original thread.

```
/fork            # branch at current point
/fork 5          # branch at message #5
/branches        # list active forks
/switch <name>   # switch to a different branch
/merge <name>    # bring a branch's changes back
```

### Data Model

Every session already has an ID.  Forks add a `parent_session_id` and a
`fork_point` (message index or UUID).

```python
class SessionMeta(BaseModel):
    session_id: str
    parent_session_id: str | None = None
    fork_point: int | None = None          # message index in parent
    branch_name: str | None = None         # user-friendly label
    created_at: datetime
    status: Literal["active", "archived"]
```

### Storage

Sessions are stored at `~/.karna/sessions/<session_id>/`.  A fork copies
messages `[0:fork_point]` into a new session directory and continues from
there.  The parent session is unmodified.

```
~/.karna/sessions/
  abc123/
    meta.json
    messages.jsonl
  def456/                     # forked from abc123 at msg 5
    meta.json                 # parent_session_id = "abc123"
    messages.jsonl            # starts with msgs 0-4 from parent
```

### Fork Mechanics

```
1. /fork [N]
2. If N given, fork_point = N; else fork_point = len(messages)
3. Create new session with parent_session_id = current session
4. Copy messages[0:fork_point] to new session
5. Switch active session to new session
6. Display: "Forked at message N. You are now on branch <name>."
```

### Branch Navigation

```
/branches
  * main (abc123) -- 12 messages
    fix-auth (def456) -- 8 messages (forked from main at msg 5)
    try-redis (ghi789) -- 6 messages (forked from main at msg 5)

/switch main
  -> loads abc123, restores conversation state

/switch fix-auth
  -> loads def456
```

### Merge

Merge is intentionally simple: it appends a summary of the branch's
divergent messages (those after the fork point) as a user message in the
target branch.  It does **not** attempt to merge tool-use side effects
(file edits, git commits) -- those must be reconciled by the user or agent.

```python
async def merge(source_branch: str, target_branch: str) -> str:
    """Summarize source's divergent messages and append to target."""
    source = load_session(source_branch)
    target = load_session(target_branch)
    fork_point = source.meta.fork_point

    divergent = source.messages[fork_point:]
    summary = await summarize_messages(divergent)

    target.messages.append(Message(
        role="user",
        content=f"[Merged from branch '{source_branch}']\n\n{summary}",
    ))
    save_session(target)
    return f"Merged {len(divergent)} messages from {source_branch}."
```

### Open Questions

1. **Deep forks:** Should we support forking a fork (tree structure)?  If so,
   how deep before the UX becomes confusing?
2. **Git integration:** When forking, should we also create a git branch?
   This would let file-level changes be properly isolated.
3. **Shared context:** After forking, if the parent adds new context (e.g.,
   reads a file), should forks be notified?
4. **Replay:** Should `/replay` re-execute all tool calls from a branch?
   This is useful for reproducibility but dangerous for side-effecting tools.

---

## 5. Collaborative Multi-Agent

### Concept

Named subagents with isolated contexts, running in parallel.  Ported from
the cc-src coordinator/worker pattern (`coordinatorMode.ts`, `Task.ts`).

```
nellie spawn reviewer --model anthropic/claude-sonnet-4-6 "review PR #42"
```

### Architecture

The parent (coordinator) conversation spawns subagents.  Each subagent gets
its own conversation, its own tool access, and optionally its own git
worktree for file isolation.

```
    Parent (Coordinator)
    /         |          \
SubAgent A   SubAgent B   SubAgent C
(reviewer)  (implementer) (tester)
   |            |            |
own conv     own conv      own conv
own tools    own tools     own tools
             worktree      worktree
```

### Interface

```python
class SubAgentConfig(BaseModel):
    name: str
    model: str | None = None                      # None = inherit parent's model
    system_prompt: str | None = None               # None = inherit parent's
    tools: list[str] | None = None                 # None = all tools
    isolation: Literal["none", "worktree", "docker"] = "none"

class SubAgentResult(BaseModel):
    agent_id: str
    name: str
    status: Literal["completed", "failed", "killed"]
    result: str
    usage: Usage
    duration_ms: int

class SubAgentHandle:
    agent_id: str
    name: str
    status: Literal["pending", "running", "completed", "failed", "killed"]

    async def send(self, message: str) -> None:
        """Send a follow-up message to a running subagent."""

    async def kill(self) -> None:
        """Stop the subagent."""

    async def wait(self) -> SubAgentResult:
        """Block until the subagent finishes."""

class SubAgentManager:
    async def spawn(
        self,
        config: SubAgentConfig,
        prompt: str,
    ) -> SubAgentHandle:
        ...

    async def list_agents(self) -> list[SubAgentHandle]:
        ...

    async def get_result(self, agent_id: str) -> SubAgentResult | None:
        ...
```

### Task Lifecycle (from cc-src Task.ts)

cc-src defines a strict FSM: `pending -> running -> completed | failed | killed`.
We adopt the same states with `isTerminalTaskStatus()` used to guard against
message injection into dead agents.

Each task gets a unique ID with a type prefix (matching cc-src's
`generateTaskId`): `a-<8 alphanumeric>` for agent tasks.

### Coordinator Pattern

When `coordinatorMode` is active (via `nellie --coordinate` or config), the
parent agent's system prompt is replaced with the coordinator prompt (adapted
from `coordinatorMode.ts`):

- The coordinator does NOT use tools directly
- It delegates to workers via `spawn` tool calls
- Worker results arrive as structured notifications (XML-tagged messages)
- The coordinator synthesizes results and communicates with the user

Key principle from cc-src: **"Workers can't see your conversation."** Every
spawn prompt must be self-contained with file paths, line numbers, and clear
success criteria.

### Isolation Modes

| Mode | Mechanism | Use case |
|------|-----------|----------|
| `none` | Shared filesystem | Read-only research tasks |
| `worktree` | `git worktree add` | Implementation tasks that write files |
| `docker` | Container with mounted volume | Untrusted code execution |

Worktree isolation creates a temporary worktree at
`~/.karna/worktrees/<agent_id>/`.  On completion, the coordinator can merge
the worktree's changes.

### Communication

Subagent results are delivered to the parent conversation as structured
messages (matching the cc-src `<task-notification>` format):

```xml
<task-notification>
  <task-id>a-x7q3m9p2</task-id>
  <status>completed</status>
  <summary>Agent "reviewer" completed</summary>
  <result>Found 3 issues in the PR: ...</result>
  <usage>
    <total_tokens>15234</total_tokens>
    <duration_ms>8200</duration_ms>
  </usage>
</task-notification>
```

### Open Questions

1. **Context sharing:** Should subagents share the parent's memory/CLAUDE.md?
   cc-src gives workers access to project context but not conversation history.
2. **Recursion depth:** Should subagents be able to spawn their own subagents?
   If so, what's the max depth?  cc-src limits to one level.
3. **Resource limits:** How do we prevent a runaway subagent from consuming
   the entire budget?  Per-agent token limits?
4. **Scratchpad:** cc-src has a scratchpad directory for cross-worker knowledge
   sharing.  Should we adopt this pattern?
5. **Concurrency model:** cc-src runs workers as forked agents sharing the
   process.  Should we use asyncio tasks, subprocesses, or both?

---

## 6. Full Compaction with LLM Summarization

### Concept

When context hits a threshold, the oldest messages are summarized by the
model and replaced with a compact summary.  Ported from cc-src's
`services/compact/` system.

### Architecture

```
     agent_loop iteration
            |
     shouldAutoCompact()
       token_count > threshold?
            |
     compactConversation()
       1. Separate: messages_to_summarize, messages_to_keep
       2. Send summarize prompt to model
       3. Replace old messages with summary
       4. Restore file attachments
            |
     Continue agent_loop with compacted context
```

### Trigger Logic (from cc-src autoCompact.ts)

```python
AUTOCOMPACT_BUFFER_TOKENS = 13_000
MAX_CONSECUTIVE_FAILURES = 3

def get_auto_compact_threshold(model: str) -> int:
    effective_window = get_context_window(model) - RESERVED_FOR_OUTPUT
    return effective_window - AUTOCOMPACT_BUFFER_TOKENS

async def should_auto_compact(
    messages: list[Message],
    model: str,
) -> bool:
    token_count = estimate_token_count(messages)
    threshold = get_auto_compact_threshold(model)
    return token_count >= threshold
```

cc-src fires compaction at ~93% of effective context window.  We adopt the
same threshold.

### Compaction Prompt

Adapted from cc-src `compact/prompt.ts`.  The summarizer is instructed to
produce:

1. **Primary request and intent** -- what the user asked for
2. **Key technical concepts** -- technologies and frameworks in play
3. **Files and code sections** -- specific files with code snippets
4. **Errors and fixes** -- what went wrong and how it was resolved
5. **User messages** -- verbatim user instructions (critical for intent drift)
6. **Pending tasks** -- what's still open
7. **Current work** -- what was happening right before compaction
8. **Next step** -- with direct quotes to prevent drift

The prompt uses `<analysis>` and `<summary>` XML tags.  The analysis block
is a scratchpad (stripped before injection); only the summary survives.

### What We Keep Post-Compaction

- System prompt (never compacted)
- Project context (CLAUDE.md, memory)
- Compact boundary marker (metadata about the compaction event)
- Summary message
- Last 5 messages (the "preserved tail")
- Recently read file contents (up to 5 files, 50K token budget)
- Active plan file
- Invoked skill content (truncated to 5K tokens/skill)

### Circuit Breaker

cc-src limits consecutive autocompact failures to 3, then stops trying for
the rest of the session.  This prevents runaway API calls when context is
irrecoverably over the limit.

```python
class CompactTracker:
    consecutive_failures: int = 0

    def should_skip(self) -> bool:
        return self.consecutive_failures >= MAX_CONSECUTIVE_FAILURES

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def record_failure(self) -> None:
        self.consecutive_failures += 1
```

### Open Questions

1. **Partial compaction:** cc-src supports partial compaction (summarize only
   messages before/after a pivot).  Do we need this on day one, or is full
   compaction sufficient?
2. **Session memory compaction:** cc-src tries session-memory compaction
   (prune messages without LLM) before falling back to LLM summarization.
   Should we implement the cheaper path first?
3. **Transcript preservation:** cc-src writes a full transcript to disk before
   compacting so users can review pre-compaction content.  Essential for us too.
4. **Tool stripping:** During compaction, cc-src strips images and
   re-injectable attachments.  We should strip large tool results (file reads,
   bash output) before sending to the summarizer.

---

## 7. Web Frontend (Next.js 15)

### Concept

A read-only (optionally read-write) web UI for browsing conversations,
replaying sessions, and monitoring costs.

### Architecture

```
    Browser (Next.js 15 + Tailwind)
           |
    HTTP + WebSocket
           |
    karna/server/ (FastAPI + WebSocket)
           |
    Session DB / Agent Loop
```

### Directory Structure

```
web/
  app/
    page.tsx                # conversation browser
    sessions/[id]/page.tsx  # session replay
    costs/page.tsx          # cost dashboard
    tools/page.tsx          # tool execution log
    memory/page.tsx         # memory inspector
    skills/page.tsx         # skills browser
  components/
    MessageBubble.tsx
    ToolResultCard.tsx
    CostChart.tsx
    SearchBar.tsx           # FTS5 search
  lib/
    api.ts                  # HTTP client
    ws.ts                   # WebSocket client for streaming
```

### Server Endpoints (karna/server/)

```python
# REST
GET    /api/sessions                    # list sessions (paginated, FTS5 search)
GET    /api/sessions/{id}               # full session with messages
GET    /api/sessions/{id}/messages      # paginated messages
GET    /api/costs                       # aggregated cost data
GET    /api/tools/log                   # recent tool executions
GET    /api/memory                      # memory entries

# Write mode (behind --write flag)
POST   /api/sessions                    # start new session
POST   /api/sessions/{id}/messages      # send user message

# WebSocket
WS     /ws/sessions/{id}/stream         # live streaming for active sessions
```

### Session Storage for Web

Current sessions are in `~/.karna/sessions/<id>/messages.jsonl`.  For the web
frontend we need indexed search.  Options:

- **SQLite + FTS5:** Single file, no server dependency, full-text search built
  in.  Messages table + FTS5 virtual table.  This is the leading option.
- **DuckDB:** Faster analytics queries but heavier dependency.

### Open Questions

1. **Authentication:** For local use, no auth needed.  For remote/team use,
   how do we authenticate?  Token-based?  OAuth?
2. **Real-time updates:** Should the web UI auto-refresh when a CLI session
   produces new messages?  WebSocket push from the server?
3. **Mobile responsive:** Is mobile a priority or desktop-only?
4. **Deployment:** Ship as `nellie web` (local dev server) or also support
   production deployment (Docker, Vercel)?
5. **Session sharing:** Should users be able to share a session URL with
   teammates for async review?

---

## 8. Gateway -- Telegram (first)

### Concept

Expose Karna as a Telegram bot.  Each Telegram chat maps to one Karna
session.  Future gateways (Slack, Discord, Matrix) follow the same adapter
pattern.

### Architecture (from Hermes Agent gateway)

```
    Telegram API
         |
    python-telegram-bot (v21+)
         |
    karna/gateway/telegram.py
         |
    GatewayAdapter (protocol)
         |
    agent_loop() / SubAgentManager
```

### Interface

```python
class GatewayAdapter(Protocol):
    """Protocol for chat platform adapters."""

    async def send_text(self, chat_id: str, text: str) -> None: ...
    async def send_image(self, chat_id: str, image_path: str) -> None: ...
    async def send_typing(self, chat_id: str) -> None: ...

class TelegramGateway:
    def __init__(self, token: str, agent_config: KarnaConfig):
        ...

    async def start(self) -> None:
        """Start polling for updates."""

    async def handle_message(self, update: Update, context: CallbackContext) -> None:
        """Route incoming message to agent loop."""
```

### Session Mapping

```python
# chat_id -> session_id mapping, persisted to ~/.karna/gateway/telegram/sessions.json
sessions: dict[str, str] = {}

async def handle_message(update, context):
    chat_id = str(update.effective_chat.id)

    if chat_id not in sessions:
        sessions[chat_id] = create_session()

    session = load_session(sessions[chat_id])

    # Add user message
    session.messages.append(Message(
        role="user",
        content=update.message.text,
    ))

    # Run agent loop
    async for event in agent_loop(provider, session, tools):
        if event.type == "text" and event.text:
            buffer += event.text
            # Flush buffer every 2 seconds (Telegram rate limits)
            if should_flush(buffer):
                await send_text(chat_id, buffer)
                buffer = ""

    # Send remaining buffer
    if buffer:
        await send_text(chat_id, buffer)
```

### Bot Commands

| Command | Action |
|---------|--------|
| `/start` | Create new session, send welcome |
| `/model <name>` | Switch active model |
| `/cost` | Show session cost so far |
| `/clear` | Archive current session, start fresh |
| `/status` | Show active loops, subagents |

### Vision Support

When a user sends an image, the gateway downloads it and passes it to the
provider as a vision input (if the model supports it).  The tool result
is rendered as a text message in Telegram.

### Rate Limiting

Telegram enforces ~30 messages/second per bot.  The gateway must:

1. Buffer streaming output and flush in chunks (every 2s or 4096 chars)
2. Use `editMessageText` for in-place updates during long responses
3. Split messages longer than 4096 characters
4. Back off on 429 errors

### Open Questions

1. **Multi-user:** Should one bot instance serve multiple users, or one bot
   per user?  Multi-user needs auth (allowlist of Telegram user IDs).
2. **Tool rendering:** Some tool results (large file reads, grep output) are
   too long for Telegram.  Should we truncate, upload as file, or link to
   the web UI?
3. **Inline keyboard:** Should we use Telegram inline keyboards for
   confirmations (e.g., "Shall I commit these changes?" [Yes] [No])?
4. **Voice:** Telegram supports voice messages.  Should we add speech-to-text
   integration?
5. **Deployment:** Run as a systemd service?  Docker container?  The bot
   needs to be always-on, unlike the CLI.

---

## Implementation Priority

| Feature | Complexity | Value | Priority |
|---------|-----------|-------|----------|
| Compaction (6) | Medium | Critical | P0 -- blocks long sessions |
| Multi-Agent (5) | High | High | P1 -- unlocks parallelism |
| Auto-Loop (1) | Medium | High | P1 -- autonomous CI/CD use case |
| Cost Routing (3) | Medium | Medium | P2 -- cost savings |
| Fork/Replay (4) | Low | Medium | P2 -- exploration UX |
| Verification (2) | Low | Medium | P3 -- nice-to-have |
| Web Frontend (7) | High | Medium | P3 -- separate project |
| Telegram (8) | Medium | Low | P4 -- after core is stable |

---

## Cross-Cutting Concerns

### Cost Tracking

All features feed into a unified `UsageTracker` that aggregates
per-session, per-loop, per-subagent, and per-verification costs.  The
tracker is the single source of truth for the budget system, cost dashboard,
and `/cost` command.

### Error Handling

Every async boundary (loop iterations, subagent runs, verification calls)
must handle:
- Provider API errors (rate limits, auth failures)
- Timeout (configurable per-feature)
- User cancellation (Ctrl+C, `/stop`)
- Context overflow (trigger compaction or fail gracefully)

### Testing Strategy

- Unit tests for classifiers, state machines, budget tracker
- Integration tests using a mock provider (deterministic responses)
- End-to-end tests for loop persistence (crash + resume)
- Property-based tests for fork/merge consistency
