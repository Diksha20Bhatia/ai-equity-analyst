# Bodhi run evidence

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
