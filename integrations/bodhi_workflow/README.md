# Bodhi Workflow Builder — the actual risk-review workflow

`risk-review-workflow.json` is the **exact** Bodhi Workflow Builder export
that `integrations/bodhi_risk_insights.py` calls and that produces every
real Bodhi run this project has generated — there is no separate,
unexecuted "design" workflow floating alongside it. What's in this file is
what runs.

```
start (Workflow Variables: Investmentportfolio, Query_for_risk_assessment)
  → Risk Analyst Agent ◄MCP bodhi-deep-research
        (live web research per stock — real, current risk factors,
         not a generic market overview)
  → Contrarian Agent (red-team challenge of the Risk Analyst's findings,
        outputs a structured per-symbol verdict: clear / caution / avoid)
  → End
```

**Why this design, and why it's deliberately small.** Two agents with a
genuine second opinion baked in — the Contrarian doesn't just repeat the
Risk Analyst, it's been seen overruling it — is enough to make the
"independent review" real. It uses node types confirmed against a real
Bodhi export (`control-start`, `control-end`, `agent-custom`,
`mcp-tools`), and every real run this project has produced came from
this exact graph.

**Getting a verifier-visible run.** Import this file into Bodhi, set
`BODHI_ENABLED=true` plus `BODHI_BASE_URL`/`BODHI_PAT_TOKEN`/
`BODHI_TASK_ID`/`BODHI_WORKFLOW_ID` in `.env` to point at your imported
copy, then trigger it through this app's own calling path —
`integrations/run_bodhi_risk_insight.py`, the dashboard's 🧠 button, or a
full `run.py` with Bodhi enabled. The real run ID Bodhi assigns is printed
to your terminal automatically (`integrations/bodhi_risk_insights.py`) —
that ID, opened from the workflow's own run list in the Bodhi UI, is what
makes the run verifier-visible: the executed node list you'd see there
matches this file exactly, because it's the same graph.

**Entry point is Workflow Variables, deliberately not a UI Node.** A UI
Node pauses a run waiting for a human to fill in a form, which breaks an
API-triggered run with no one there to answer it. Workflow Variables are
the entry point an API call can actually set — see
`BODHI_PORTFOLIO_VARIABLE` / `BODHI_QUERY_VARIABLE` in `.env.example`.
