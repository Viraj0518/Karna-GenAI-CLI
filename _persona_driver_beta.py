"""Run beta's 10 Karna personas through nellie-as-subagent via MCP — in parallel.

Data Science (6) + Engineering (4). Each persona gets its own MCP server
subprocess so they can run concurrently. ThreadPoolExecutor coordinates.
I/O-bound (OpenRouter latency dominates), so threads are fine.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PARENT = Path("/tmp/karna-personas-beta")
MODEL = "openrouter:anthropic/claude-haiku-4.5"
MAX_ITERS = 20
MAX_WORKERS = 10  # one per persona — all I/O bound on OpenRouter
PRINT_LOCK = threading.Lock()

PERSONAS = [
    # ─── Data Science (6) ──────────────────────────────────────────────
    {
        "slug": "10-chief-data-scientist",
        "role": "Chief Data Scientist at Karna. Leads methods design for CDC-facing surveys and dashboards. Rigorous on sampling, weighting, and variance estimation.",
        "task": """Design a sampling frame for a national KAP (Knowledge/Attitude/Practices) survey on vaccine hesitancy, target n=3000.

Write `sampling_frame.md` in the workspace. Include:
- Stratification strategy (named strata + rationale)
- Cluster design (PSU definition, cluster size, expected design effect)
- Design-effect assumption (explicit number with source/reasoning)
- A 3-row sample-size allocation table (stratum | target n | justification)

End with one sentence confirming the file is written.""",
    },
    {
        "slug": "11-senior-statistician",
        "role": "Senior Statistician at Karna. Reviews survey weights, stratum allocation, and precision for NHANES-adjacent work.",
        "task": """Write `weights_review.md`: a memo reviewing survey weights for a simulated NHANES 2023 extract.

Fabricate plausible weight-distribution summaries (mean, median, 95th pct, CV, sum-of-weights).
Flag any stratum with design effect > 2.0 that warrants reweighting.
Include a 5-row stratum table (stratum | n_sampled | sum_wt | deff | reweight_flag).
Close with a 2-sentence recommendation for the survey ops team.""",
    },
    {
        "slug": "12-biostatistician",
        "role": "Biostatistician at Karna. Comfortable with GLMMs, model diagnostics, and plain-language reporting to non-stats audiences.",
        "task": """Generate a small synthetic dataset (patients nested in clinics, outcome = 30-day readmission).

Fit a multilevel logistic regression. Save:
- `blogit_analysis.py`: the Python script (statsmodels or scikit-learn + a random-effect by clinic). Must run end-to-end.
- `blogit_report.md`: coefficients with 95% CIs, odds ratios, ICC/variance component, and a one-paragraph plain-language interpretation for a non-stats reader.
Use ≥200 synthetic rows across ≥5 clinics.""",
    },
    {
        "slug": "13-survey-methodologist",
        "role": "Survey Methodologist at Karna. Cognitively tests instruments for clarity, bias, and measurement error.",
        "task": """First write `questionnaire.md`: a 14-item questionnaire covering vaccine intent, trust in CDC, and primary info sources. Include response scales.

Then write `cog_test_report.md`: flag any items with comprehension risk, double-barrel wording, socially-desirable bias, or acquiescence-bias risk. Reference items by number. End with 3 concrete revision recommendations.""",
    },
    {
        "slug": "14-data-engineer",
        "role": "Data Engineer at Karna. Ships ETL against XPT/SAS + public-health data formats. Pragmatic on schema and lineage.",
        "task": """Build `nchs_etl.py`: a Python pipeline stub that reads a simulated NHANES SAS XPT file → converts to Parquet → writes to a local `./warehouse/` directory.

Use pandas + pyarrow. Instead of requiring a real XPT, create a small synthetic fixture (≥50 rows, ≥8 cols mirroring NHANES demographics) inside the script under `if __name__ == "__main__"`.
Also write `README.md` in the workspace: the DAG (sources → transforms → sinks), one-line per step, and how to extend to additional XPT files.""",
    },
    {
        "slug": "15-ml-engineer",
        "role": "ML Engineer at Karna. Builds weak-signal classifiers for public-health surveillance under compute + data constraints.",
        "task": """Prototype `outbreak_signal.py`: a scikit-learn classifier on a synthetic NNDSS-shaped dataset you generate inside the script (weekly case counts × disease × jurisdiction, ≥500 rows, ≥4 diseases, ≥5 jurisdictions).

Target: anomalous-week flag (binary). Use a simple baseline model (logistic regression or gradient boosting — your call). Print precision / recall on a held-out 20% at a reasonable threshold. Persist the fitted model via joblib as `outbreak_model.joblib`.""",
    },
    # ─── Engineering (4) ───────────────────────────────────────────────
    {
        "slug": "16-platform-engineer",
        "role": "Platform Engineer at Karna. Owns internal dashboards and small services that expose program-management data to ops.",
        "task": """Build `contract_dashboard/`: a single-file Flask dashboard at `contract_dashboard/app.py`.

Reads a synthetic `contracts.json` you generate alongside (≥6 contracts with ceiling, burn %, remaining).
Renders a table at `/` with columns (contract, ceiling $, burn %, remaining $).
Start the server in the background, curl `http://localhost:5000/` to prove it works, kill it. Save the curl output as `smoke.txt`.""",
    },
    {
        "slug": "17-security-engineer",
        "role": "Security Engineer at Karna. FISMA + NIST 800-53 fluent; writes gap reports for federal customers.",
        "task": """Write `fisma_gap_report.md`: a FISMA-Moderate self-assessment of a hypothetical Karna analytics stack (Postgres on-prem, Python ETL, shared file drop, internal dashboards).

Output an 8-row gap-remediation table keyed to specific NIST 800-53 controls (control ID | control name | current state | gap | remediation | effort S/M/L).
End with a 3-bullet executive summary highlighting the highest-priority gap.""",
    },
    {
        "slug": "18-sre",
        "role": "SRE at Karna. Writes runbooks that oncall can execute at 3am without paging a senior.",
        "task": """Write `runbooks.md`: 3 production-failure runbooks for a CDC-facing service.

Scenarios:
1. Postgres DB replication lag
2. Ingest pipeline stalls
3. Auth provider (e.g. PIV/Okta) outage

Each runbook must have 4 sections: Detect (how you notice), Diagnose (first 3 checks), Mitigate (ordered steps), Post-incident (what to capture). Keep each runbook ≤300 words.""",
    },
    {
        "slug": "19-junior-developer",
        "role": "Junior Developer at Karna. Refactors existing code for readability + adds unit tests while preserving behavior exactly.",
        "task": """Create `nchs_etl_stub.py` first (a tiny ETL stub with a transformation step — e.g. reads a CSV fixture, renames columns, computes one derived column, writes Parquet). Keep it ≤60 lines.

Then refactor it into `nchs_etl_refactored.py` for readability (extract the transformation into a pure function).
Add `test_nchs_etl.py` with ≥3 pytest cases covering the transformation.
Verify pre/post behavior is identical by running both scripts on the same fixture and diffing outputs — save the diff as `verify.txt` (empty file = success).""",
    },
]


def log(msg: str):
    with PRINT_LOCK:
        print(msg, flush=True)


def send(p, obj):
    p.stdin.write(json.dumps(obj) + "\n")
    p.stdin.flush()


def recv(p):
    return p.stdout.readline().strip()


def drain_stderr(p, bucket):
    for line in iter(p.stderr.readline, ""):
        bucket.append(line)


def run_persona(persona: dict) -> dict:
    """Each thread owns a dedicated MCP server subprocess."""
    slug = persona["slug"]
    ws = PARENT / slug
    ws.mkdir(parents=True, exist_ok=True)

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
    t_err = threading.Thread(target=drain_stderr, args=(p, errs), daemon=True)
    t_err.start()

    log(f"[{slug}] spawning MCP server pid={p.pid}")
    send(p, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    handshake = recv(p)
    if not handshake:
        log(f"[{slug}] ❌ empty handshake")
        return {"slug": slug, "duration_s": 0, "is_error": True, "files_produced": [], "transcript_len": 0}

    prompt = f"""You are acting as: **{persona["role"]}**

Task:
{persona["task"]}

Be concise. Produce real files in the workspace. Finish with one line
confirming completion."""

    t0 = time.time()
    log(f"[{slug}] nellie_agent call started")
    send(
        p,
        {
            "jsonrpc": "2.0",
            "id": 2,
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
    line = recv(p)
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
    (ws / "_stderr.log").write_text("".join(errs[-200:]), encoding="utf-8")

    # Shutdown this worker's server
    try:
        send(p, {"jsonrpc": "2.0", "id": 99, "method": "shutdown", "params": {}})
        recv(p)
    except Exception:
        pass
    try:
        p.stdin.close()
        p.wait(timeout=5)
    except Exception:
        p.kill()

    files = sorted([q.name for q in ws.iterdir() if not q.name.startswith("_")])
    log(f"[{slug}] ✅ done in {dt:.1f}s · isError={is_error} · files={files}")
    return {
        "slug": slug,
        "duration_s": round(dt, 1),
        "is_error": is_error,
        "files_produced": files,
        "transcript_len": len(transcript),
    }


def main():
    PARENT.mkdir(parents=True, exist_ok=True)
    log(f"== launching {len(PERSONAS)} personas × {MAX_WORKERS} workers ==")
    wall_start = time.time()
    scoreboard = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(run_persona, p): p["slug"] for p in PERSONAS}
        for f in as_completed(futures):
            scoreboard.append(f.result())
    wall = time.time() - wall_start

    # Sort by slug so output is deterministic regardless of completion order.
    scoreboard.sort(key=lambda e: e["slug"])

    scorecard = PARENT / "_beta_scorecard.md"
    lines = [
        "# Beta persona scorecard (Data Science + Engineering)",
        "",
        f"Wall time: {wall:.1f}s · {MAX_WORKERS}-thread parallel · model {MODEL}",
        "",
        "| Slug | Duration | Error? | Files produced |",
        "|------|----------|--------|----------------|",
    ]
    for e in scoreboard:
        flag = "❌" if e["is_error"] else "✅"
        files = ", ".join(e["files_produced"]) or "—"
        lines.append(f"| {e['slug']} | {e['duration_s']}s | {flag} | {files} |")
    total = sum(e["duration_s"] for e in scoreboard)
    ok = sum(1 for e in scoreboard if not e["is_error"])
    lines += [
        "",
        f"**Summary:** {ok}/{len(scoreboard)} clean • sequential-equivalent {round(total, 1)}s • wall {wall:.1f}s",
    ]
    scorecard.write_text("\n".join(lines), encoding="utf-8")
    log("== done ==")
    log(f"scorecard: {scorecard}")
    log(f"ok: {ok}/{len(scoreboard)} · sequential-sum {round(total, 1)}s · wall {wall:.1f}s")


if __name__ == "__main__":
    main()
