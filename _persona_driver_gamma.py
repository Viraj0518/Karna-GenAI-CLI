"""Run gamma's 11 Karna personas through nellie-as-subagent via MCP.

Research (3) + Health Comms (4) + Support/Ops (4) = 11 personas.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PARENT = Path("/tmp/karna-personas-gamma")
MODEL = "openrouter:anthropic/claude-haiku-4.5"
MAX_ITERS = 20

PERSONAS = [
    # ── Research (3) ──
    {
        "slug": "20-principal-investigator",
        "role": "Principal Investigator at Karna. Designs and leads CDC-funded qualitative research studies on public health communications.",
        "task": """Draft protocol_skeleton.md: a 30-focus-group qualitative study protocol on maternal-health communications in low-SES urban populations. Sections: Background (1 para), Aims (3), Design, Recruitment, Data Collection, Analysis Plan, IRB Considerations, Timeline. ≤ 1000 words.""",
    },
    {
        "slug": "21-research-methodologist",
        "role": "Research Methodologist at Karna. Reviews systematic review protocols for CDC evaluation contracts.",
        "task": """Write prospero_review.md: a structured critique of a hypothetical systematic-review protocol. Check: PRISMA alignment, search strategy completeness, inclusion criteria specificity, risk-of-bias tooling. Flag 5 issues with recommended fixes.""",
    },
    {
        "slug": "22-field-coordinator",
        "role": "Field Coordinator at Karna. Plans and executes multi-site data collection for CDC research contracts.",
        "task": """Write fieldwork_plan.md: logistics for 6 cities × 5 focus groups each, 4-week recruitment window. Output a table per week (recruitment targets, facility needs, moderator assignments) and a risk register with 5 risks + mitigation.""",
    },
    # ── Health Communications (4) ──
    {
        "slug": "23-senior-risk-communicator",
        "role": "Senior Risk Communicator at Karna. Develops CERC-aligned messaging for CDC public health announcements.",
        "task": """Write cerc_talking_points.md for a hypothetical CDC announcement about a newly-identified vaccine-safety signal. Audience: primary-care clinicians. Follow CERC principles (Be First, Be Right, Be Credible, Express Empathy, Promote Action, Show Respect). 5 talking points + 3 anticipated clinician FAQs.""",
    },
    {
        "slug": "24-health-writer",
        "role": "Health Writer at Karna. Adapts NCHS statistical publications for different reading levels.",
        "task": """Rewrite a sample NCHS fact sheet for 8th-grade reading level without losing statistical precision. First write original.md (6th-grade level, 250 words about childhood obesity rates). Then rewritten.md at 8th-grade level. Then diff_analysis.md noting Flesch-Kincaid before/after + precision-preservation notes.""",
    },
    {
        "slug": "25-plain-language-editor",
        "role": "Plain Language Editor at Karna. Ensures all CDC-facing materials meet 508 accessibility and plain language standards.",
        "task": """Run a 508/plain-language pass on a fabricated COVID booster messaging kit (write kit_original.md with 3 paragraphs first). Output kit_revised.md + diff.md with Flesch-Kincaid delta + list of 508 issues fixed.""",
    },
    {
        "slug": "26-behavioral-scientist",
        "role": "Behavioral Scientist at Karna. Applies KAP frameworks to qualitative data from CDC vaccine confidence studies.",
        "task": """Code a fabricated set of 6 focus-group transcript excerpts (you write them yourself as transcripts.md, each ~100 words on vaccine trust) using a KAP codebook you also write (codebook.md — Knowledge / Attitudes / Practices with 3 sub-codes each). Output coded_results.md with theme frequencies.""",
    },
    # ── Support / Operations (4) ──
    {
        "slug": "27-it-support-l1",
        "role": "IT Support Analyst (L1) at Karna. First-line support for internal tools and Privy-based authentication.",
        "task": """Write ticket_resolution.md: a walkthrough for resetting Privy session cookies for a user who can't access their vault. Format: user-friendly step-by-step plus a verification checklist plus an escalation trigger ('if step 4 still fails, escalate to L2').""",
    },
    {
        "slug": "28-senior-it-admin",
        "role": "Senior IT Admin (L2/L3) at Karna. Investigates infrastructure incidents and writes root cause analyses.",
        "task": """Investigate a fabricated FISMA log-shipper dropout (last Tuesday 22:00-02:00 UTC). Write rca_report.md with timeline, root cause hypothesis, supporting evidence, corrective actions. You can invent the specifics but be internally consistent.""",
    },
    {
        "slug": "29-contract-admin",
        "role": "Contract Admin at Karna. Tracks deliverables and funds across multiple federal contract vehicles.",
        "task": """Generate monthly_cdr.md: a Contract Deliverables Report for a fabricated month. Include 4 contract lines (CIO-SP3, SPARC, NCIRD ICRA, OADC BPA) × funds drawn / delivered / pending / past-due / notes. Total the columns.""",
    },
    {
        "slug": "30-data-steward",
        "role": "Data Steward at Karna. Maps data lineage and ensures PHI/PII compliance across CDC-facing pipelines.",
        "task": """Write lineage_audit.md: map PII/PHI lineage for a fabricated NCIRD-facing data pipeline (ingest → transform → warehouse → dashboard). Flag unencrypted hops, retention-policy gaps, and access-control weaknesses. Recommend 5 remediation items.""",
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
    prompt = f"""You are acting as: **{persona["role"]}**

Task:
{persona["task"]}

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
    PARENT.mkdir(parents=True, exist_ok=True)
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
        print(f"  duration={entry['duration_s']}s  isError={entry['is_error']}  files={entry['files_produced']}")
        sys.stdout.flush()

    send(p, {"jsonrpc": "2.0", "id": 99, "method": "shutdown", "params": {}})
    recv(p)
    p.stdin.close()
    p.wait(timeout=10)

    # Write scorecard
    scorecard = PARENT / "_gamma_scorecard.md"
    lines = [
        "# Gamma persona scorecard",
        "",
        "| Slug | Duration | Error? | Files produced |",
        "|------|----------|--------|----------------|",
    ]
    for e in scoreboard:
        flag = "\u274c" if e["is_error"] else "\u2705"
        files = ", ".join(e["files_produced"]) or "\u2014"
        lines.append(f"| {e['slug']} | {e['duration_s']}s | {flag} | {files} |")
    total = sum(e["duration_s"] for e in scoreboard)
    ok = sum(1 for e in scoreboard if not e["is_error"])
    lines += [
        "",
        f"**Summary:** {ok}/{len(scoreboard)} clean \u2022 total {round(total, 1)}s",
    ]
    scorecard.write_text("\n".join(lines), encoding="utf-8")
    print("\n=== done ===")
    print(f"scorecard: {scorecard}")
    print(f"ok: {ok}/{len(scoreboard)}, total {round(total, 1)}s")


if __name__ == "__main__":
    main()
