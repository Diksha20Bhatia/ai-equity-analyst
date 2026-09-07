"""
collect_run_evidence.py  —  generate real Bodhi execution evidence
=====================================================================
    uv run python integrations/bodhi_workflow/collect_run_evidence.py

Reads output/metrics.jsonl for "bodhi_run" events — each one written by
integrations/bodhi_risk_insights.py at the moment a REAL Bodhi API call
returns (see call_bodhi_workflow() in bodhi_client.py, which captures the
run id Bodhi itself assigns) — and writes RUN_EVIDENCE.md listing every
real run this app has actually made.

This script never invents a run id. If output/metrics.jsonl has no
"bodhi_run" events yet, RUN_EVIDENCE.md says so explicitly and gives the
exact steps to produce one. There is no other way to get a real Bodhi run
id into this file — it has to come from Bodhi actually executing.

WHAT PRODUCES A "bodhi_run" EVENT (any one of these, with BODHI_ENABLED=true
and real BODHI_* credentials in .env):
  - a full pipeline run:            uv run python run.py
  - re-reviewing the last shortlist: uv run python integrations/run_bodhi_risk_insight.py
  - the dashboard's 🧠 Bodhi button

To also exercise the workflow's native Repeat/Switch/Preview nodes (not
just the Python-side per-symbol loop), the run needs BOTH:
  1. the expanded workflow (integrations/bodhi_workflow/workflow-AI-Equity-
     Analyst-846bb836-...json) actually built/imported in Bodhi's canvas
     and its BODHI_WORKFLOW_ID in .env, and
  2. BODHI_PER_SYMBOL_RUNS=false in .env, so this app sends the whole
     shortlist as one call and lets the platform's own Repeat node iterate.
"""

import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import observability  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent / "RUN_EVIDENCE.md"

_NO_EVIDENCE_BODY = """# Bodhi run evidence

**No real Bodhi run has been recorded yet.** This file is generated —
never hand-edited — from `output/metrics.jsonl`, which only gains a
`bodhi_run` entry when `integrations/bodhi_risk_insights.py` makes an
actual, successful (or attempted) call to the real Bodhi API and captures
the run id Bodhi itself returns. Nothing here is invented.

## How to produce a real run

1. Set real credentials in `.env`: `BODHI_ENABLED=true`, `BODHI_BASE_URL`,
   `BODHI_PAT_TOKEN`, `BODHI_TASK_ID`, `BODHI_WORKFLOW_ID`.
2. Run the pipeline once so there's a shortlist to review:
   `uv run python run.py`
   (this alone already triggers a real Bodhi review as step 6, if enabled)
3. Or, to review the most recent shortlist again without a full re-run:
   `uv run python integrations/run_bodhi_risk_insight.py`
4. Re-run this script: `uv run python integrations/bodhi_workflow/collect_run_evidence.py`

## To also prove the Repeat/Switch/Preview nodes executed

Set `BODHI_PER_SYMBOL_RUNS=false` in `.env` — this sends the whole
shortlist as ONE call, letting the platform's own Repeat node drive the
loop, instead of this app looping in Python. This requires the expanded
14-node workflow to actually be built/imported in Bodhi first (see
`README.md` in this folder for the fallback ladder if any inferred node
type doesn't import cleanly) and its real `BODHI_WORKFLOW_ID` in `.env`.
"""


def build_evidence() -> str:
    metrics = observability.read_metrics()
    runs = [m for m in metrics if m.get("event") == "bodhi_run" and m.get("bodhi_run_id")]

    if not runs:
        return _NO_EVIDENCE_BODY

    by_mode = {"per_symbol": [], "platform_loop": []}
    for r in runs:
        by_mode.setdefault(r.get("mode", "per_symbol"), []).append(r)

    lines = [
        "# Bodhi run evidence",
        "",
        f"**Auto-generated** {dt.datetime.now().isoformat(timespec='seconds')} from "
        f"`output/metrics.jsonl` — real `bodhi_run` events only. Regenerate with "
        f"`uv run python integrations/bodhi_workflow/collect_run_evidence.py` after any new run.",
        "",
        f"**{len(runs)} real Bodhi run(s) recorded** across "
        f"{len({r.get('bodhi_run_id') for r in runs})} distinct run id(s).",
        "",
    ]

    if by_mode.get("platform_loop"):
        lines += [
            "## Platform-loop mode (`BODHI_PER_SYMBOL_RUNS=false`)",
            "",
            "One run id drove the workflow's own Repeat/Switch/Preview nodes across "
            "every symbol below — this is the evidence that exercises the platform "
            "constructs directly, not just the Python calling loop.",
            "",
            "| Timestamp | Run ID | Symbol | Status | Decision |",
            "|---|---|---|---|---|",
        ]
        for r in by_mode["platform_loop"]:
            lines.append(
                f"| {r.get('timestamp', '')} | `{r.get('bodhi_run_id')}` | "
                f"{r.get('symbol', '?')} | {r.get('status', '?')} | {r.get('decision', '?')} |"
            )
        lines.append("")

    if by_mode.get("per_symbol"):
        lines += [
            "## Per-symbol mode (`BODHI_PER_SYMBOL_RUNS=true`, default)",
            "",
            "One independent run id per symbol — each is its own, separately "
            "auditable Bodhi execution.",
            "",
            "| Timestamp | Run ID | Symbol | Status | Decision |",
            "|---|---|---|---|---|",
        ]
        for r in by_mode["per_symbol"]:
            lines.append(
                f"| {r.get('timestamp', '')} | `{r.get('bodhi_run_id')}` | "
                f"{r.get('symbol', '?')} | {r.get('status', '?')} | {r.get('decision', '?')} |"
            )
        lines.append("")

    distinct_ids = sorted({r.get("bodhi_run_id") for r in runs})
    lines += [
        "## For the certification submission",
        "",
        "Real run id(s) to cite:",
        "",
    ] + [f"- `{rid}`" for rid in distinct_ids]

    return "\n".join(lines)


if __name__ == "__main__":
    content = build_evidence()
    OUT_PATH.write_text(content)
    print(content)
    print(f"\nSaved -> {OUT_PATH}")
