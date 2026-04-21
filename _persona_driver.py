"""Run alpha's 9 Karna personas through nellie-as-subagent via MCP.

Per persona: load role system prompt, hand nellie a task, capture the
transcript + halt reason + files produced, write a scorecard entry.
"""

from __future__ import annotations
import json, os, subprocess, sys, threading, time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PARENT = Path("C:/Users/12066/karna-personas")
MODEL = "openrouter:anthropic/claude-haiku-4.5"
MAX_ITERS = 20

# Alpha's 9: Leadership (4) + BD (5). Each persona gets a one-line role
# brief + a canonical Karna-specific task. The brief is injected into
# the prompt as "Role:" context so the agent has the persona hat on.
PERSONAS = [
    {
        "slug": "01-ceo",
        "role": "CEO of Karna LLC, a top-10 CDC contractor and 8(a) EDWOSB. Concerned with strategy, ceiling exposure, and board-level reporting.",
        "task": """Generate a concise CEO briefing. Write it as ceo_brief.md in the workspace.

Content required:
- Top 5 active CDC task orders (synthesise realistic Karna examples — don't invent specific values we can verify)
- For each: ceiling $, funds drawn %, risk flag (GREEN/YELLOW/RED)
- One paragraph: which contract vehicle is closest to saturation and what to do about it.

Finish with one sentence confirming the file is written.""",
    },
    {
        "slug": "02-coo",
        "role": "COO at Karna. Owns delivery across multiple contract vehicles (CIO-SP3, SPARC IDIQ, NCIRD ICRA, OADC BPA). Drives deliverable schedules.",
        "task": """Write delivery_audit.md: a table of 8 fictional-but-realistic active deliverables with columns (contract, deliverable, due date, status). Flag any with status=OVERDUE in bold. End with a one-sentence remediation plan for the overdue items.""",
    },
    {
        "slug": "03-cfo",
        "role": "CFO at Karna. Tracks burn rates, contract ceilings, and rate cards across NAICS 541611, 541690, 541720, 541910, 518210, 541512.",
        "task": """Write q4_ceiling_exposure.md: a short memo projecting which of our 4 contract vehicles (CIO-SP3 / SPARC / NCIRD ICRA / OADC BPA) will hit 80% ceiling in Q4. Include a 3-row projection table (vehicle, current burn %, projected EoQ %). End with a funding-mod recommendation.""",
    },
    {
        "slug": "04-chief-scientist",
        "role": "Chief Scientist at Karna. Reviews methodology for CDC-facing proposals; background in biostatistics and survey methods.",
        "task": """Write methodology_review.md: a structured critique of a proposed NCIRD ICRA response. Assume the proposal uses a stratified cluster sample of n=2400 across 8 regions. Output: 3 strengths, 3 concerns, 2 recommended methodological additions. Keep under 400 words.""",
    },
    {
        "slug": "05-bd-director",
        "role": "BD Director at Karna. Hunts federal set-asides in NAICS 541611/541720, coordinates capture pipeline.",
        "task": """Write bd_pipeline.md: a short capture-pipeline view for this week. Include: (1) 5 hypothetical open 8(a) set-asides in NAICS 541611/541720 with title, agency, est. value, closing date; (2) a rank by Karna fit score (1-10) with one-line rationale; (3) a go/no-go recommendation on the top one.""",
    },
    {
        "slug": "06-capture-manager",
        "role": "Capture Manager at Karna. Converts identified opps into win-able proposals via past-performance matching and teaming decisions.",
        "task": """Write capture_plan.md: a capture plan for a hypothetical CDC OADC task order on 'vaccine hesitancy communications research'. Sections: Opportunity summary, Win themes (3), Past performance citations (3 from Karna's real past: WTC, CDC COVID-19 VTF, Ebola response), Teaming targets (2 named firms), Discriminators (3 bullets).""",
    },
    {
        "slug": "07-proposal-writer",
        "role": "Proposal Writer at Karna. Drafts Technical Approach and Management sections under tight SOW constraints.",
        "task": """Write tech_approach_draft.md: a Technical Approach section (~600 words) for an NCHS statistical programming task order. Include: (1) understanding of requirement (1 paragraph), (2) phased approach with named milestones (3 phases), (3) quality-control plan (1 paragraph), (4) risk mitigation (3 risks, mitigation each). Tone: compliant, past-performance-backed, not-salesy.""",
    },
    {
        "slug": "08-past-performance-analyst",
        "role": "Past Performance Analyst at Karna. Maintains the PP library, writes CPAR-style narratives.",
        "task": """Write pp_narrative_wtc.md: a past-performance narrative for the WTC Health Program (Karna is prime TPA, 2016-present). Format: Contract Number | Customer | Period | Value | Scope (2-3 sentences) | Performance Outcomes (3 bullets). ≤ 500 words. Tone: CPAR-aligned.""",
    },
    {
        "slug": "09-eight-a-compliance",
        "role": "8(a) Compliance Officer at Karna. Tracks SBA certification, set-aside eligibility, and size-standard compliance.",
        "task": """Write compliance_checklist.md: a 30-day compliance checklist. Include: (1) SBA 8(a) certification renewal status + next milestone, (2) size-standard check across our NAICS codes, (3) 5 items to verify before bidding next set-aside. Format as a table with due dates + owner placeholders.""",
    },
]


def send(p, obj):
    p.stdin.write(json.dumps(obj) + "\n")
    p.stdin.flush()


def recv(p):
    return p.stdout.readline().strip()


def drain_stderr(p, bucket):
    for line in iter(p.stderr.readline, ""):
        bucket.append(line)


def run_persona(persona: dict, server_proc, call_id: int) -> dict:
    slug = persona["slug"]
    ws = PARENT / slug
    ws.mkdir(parents=True, exist_ok=True)
    prompt = f"""You are acting as: **{persona['role']}**

Task:
{persona['task']}

Be concise. Produce real files in the workspace. Finish with one line
confirming completion."""
    t0 = time.time()
    send(
        server_proc,
        {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {
                "name": "nellie_agent",
                "arguments": {
                    "prompt": prompt,
                    "model": MODEL,
                    "max_iterations": MAX_ITERS,
                    "workspace": str(ws),
                    "include_events": True,
                },
            },
        },
    )
    line = recv(server_proc)
    dt = time.time() - t0
    try:
        reply = json.loads(line)
        result = reply.get("result", {})
        is_error = result.get("isError", False)
        content = result.get("content", [])
        transcript = content[0]["text"] if content else ""
        events_json = content[1]["text"] if len(content) > 1 else ""
    except Exception as e:
        is_error = True
        transcript = f"(parse failed: {e})"
        events_json = ""

    (ws / "_transcript.md").write_text(transcript, encoding="utf-8")
    (ws / "_events.json").write_text(events_json, encoding="utf-8")
    files = sorted([p.name for p in ws.iterdir() if not p.name.startswith("_")])
    return {
        "slug": slug,
        "duration_s": round(dt, 1),
        "is_error": is_error,
        "files_produced": files,
        "transcript_len": len(transcript),
    }


def main():
    env = os.environ.copy()
    p = subprocess.Popen(
        [sys.executable, "-m", "karna.cli", "mcp", "serve"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
    )
    errs: list[str] = []
    threading.Thread(target=drain_stderr, args=(p, errs), daemon=True).start()
    send(p, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    print("handshake:", recv(p)[:100])
    sys.stdout.flush()

    scoreboard = []
    for i, persona in enumerate(PERSONAS, start=2):
        print(f"\n=== {persona['slug']} ===")
        sys.stdout.flush()
        entry = run_persona(persona, p, i)
        scoreboard.append(entry)
        print(
            f"  duration={entry['duration_s']}s  isError={entry['is_error']}  "
            f"files={entry['files_produced']}"
        )
        sys.stdout.flush()

    send(p, {"jsonrpc": "2.0", "id": 99, "method": "shutdown", "params": {}})
    recv(p)
    p.stdin.close()
    p.wait(timeout=10)

    # Write scorecard
    scorecard = PARENT / "_alpha_scorecard.md"
    lines = [
        "# Alpha persona scorecard",
        "",
        "| Slug | Duration | Error? | Files produced |",
        "|------|----------|--------|----------------|",
    ]
    for e in scoreboard:
        flag = "❌" if e["is_error"] else "✅"
        files = ", ".join(e["files_produced"]) or "—"
        lines.append(
            f"| {e['slug']} | {e['duration_s']}s | {flag} | {files} |"
        )
    total = sum(e["duration_s"] for e in scoreboard)
    ok = sum(1 for e in scoreboard if not e["is_error"])
    lines += [
        "",
        f"**Summary:** {ok}/{len(scoreboard)} clean • total {round(total, 1)}s",
    ]
    scorecard.write_text("\n".join(lines), encoding="utf-8")
    print("\n=== done ===")
    print(f"scorecard: {scorecard}")
    print(f"ok: {ok}/{len(scoreboard)}, total {round(total, 1)}s")


if __name__ == "__main__":
    main()
