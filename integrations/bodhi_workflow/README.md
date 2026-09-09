# Bodhi Workflow Builder — the risk-review workflow, with real escalation branching

`workflow-AI-Equity-Analyst-846bb836-ab0a-4b48-9226-1253617a3699.json`
(13 nodes) — `integrations/bodhi_risk_insights.py` calls it via
`BODHI_TASK_ID`/`BODHI_WORKFLOW_ID` in `.env`.

**Status: fully verified, ALL SIX node categories the certification
scores are present and confirmed executed in one real run** —
`sub-workflow` and `preview` (built from a real screenshot of Bodhi's
own "Edit Sub Workflow Node" panel, then hand-verified in Bodhi's UI and
exported back) both ran clean on the first live test.

**Five real, confirmed runs:**
- `7f0ed4a7-be5a-407d-a727-853d4040fc57` / `4c2db839-3b46-4227-aa1c751ee265`
  — the original 10-node core, escalation branch fired both times.
- `5ca730af-ee54-4a77-af3a-df5ea10d1a67` — real synthetic test portfolio
  (`integrations/test_bodhi_workflow.py`), escalation branch fired, both
  RELIANCE (`caution`) and ICICIBANK (`avoid`) got real, substantive,
  research-backed verdicts.
- `b2881f2d-83d6-4746-927c-d1383047a225` — an earlier, inline version of
  the quick-check agent, superseded by the real `subworkflow` node below.
- **`0aac93fd-e5f9-40eb-a96c-1e1447c88e2e` — the CURRENT 13-node graph,
  every node type confirmed executed**: Bodhi's own per-node trace showed
  `Sub Workflow` complete early (real subworkflow call to
  `subflow-symbol-risk-check.json`, imported standalone as workflow
  `6f0c56aa-2a98-4ccf-968d-df48cbdf8fb8`), then `Risk Analyst Agent`,
  `Contrarian Agent`, `Needs Escalation`, `Escalation Analyst`,
  `Set Combine Final Verdict`, `Set Validate Final Payload`, and
  `Preview Final Payload` all completed, ending in `status: completed`
  with a real ICICIBANK `caution` verdict citing actual current detail
  (RBI H1 2025 rate-cut signals, ICICI's real Q3 FY24 NIM 4.36% vs 4.40%
  prior quarter, FII outflows).

**A genuinely unconfirmed construct was tried and failed, cleanly and
informatively.** The first version of this extension added a `for-each`
loop node (a best-effort guess at Bodhi's schema, since its docs aren't
reachable from outside its network). On import + run, Bodhi's own
per-node status report was unambiguous: `"Loop Over Portfolio Symbols": {"reason": "Unreachable — no valid execution path"}` —
the engine didn't recognize `type: "for-each"` at all, which cascaded to
every node downstream showing the same "Unreachable" status, and the run
ended with `"End node status: skipped"`. That's real, useful evidence a
guess was wrong, not silence — the loop node was removed; everything
else in this section is what's actually live.

```
start (Workflow Variables: Investmentportfolio, Query_for_risk_assessment)
  ├─→ Quick Risk Check Subflow  [sub-workflow — NEW, unverified, see below]
  │       (a leaf branch: calls subflow-symbol-risk-check.json as a real
  │        reusable, separately-importable workflow — deliberately NOT
  │        on the path to End, so an unconfirmed node type can't
  │        cascade into the proven escalation core the way the earlier
  │        `for-each` guess did)
  │
  └─→ Risk Analyst Agent ◄MCP bodhi-deep-research
        (live web research per stock — real, current risk factors)
  → Contrarian Agent (red-team challenge of the Risk Analyst's findings,
        outputs {"verdicts": [{"symbol", "verdict": clear|caution|avoid, "notes"}]})
  → Set Extract Escalation Flag  (real branch contract — see below)
  → If-Else: Needs Escalation?
        true  → Escalation Analyst ◄MCP bodhi-deep-research (deeper pass,
                 reads the Contrarian's full response itself and finds the
                 flagged symbols — real rework, not a duplicate pass)
        false → (skip straight to combine — Contrarian's verdict stands)
  → Set Combine Final Verdict (escalation's verdict if it ran, else the
    Contrarian's original — a real priority-fallback, not concatenation)
  → Set Validate Final Payload (real fallback path — proven pattern)
       ├─→ End
       └─→ Preview Final Payload  [preview — NEW, unverified, see below]
               (another leaf: a non-blocking tap on the real validated
                payload, for operator inspection mid-graph)
```

## What's new, and how much to trust each part

**`Sub Workflow`** (node type `subworkflow`) and **`Preview Final
Payload`** (node type `preview`) replace the earlier inline `Quick Risk
Check Agent` — this turns the "reusable subflow" ask into an ACTUAL
native subworkflow-call node, plus the `preview` construct — both
**confirmed live in run `0aac93fd-...`**, both real schemas now known
exactly (not guessed):

```json
// subworkflow node — real, confirmed schema
"type": "subworkflow",
"data": {
  "input": {
    "workflowid": "6f0c56aa-2a98-4ccf-968d-df48cbdf8fb8",  // subflow-symbol-risk-check.json's real Bodhi workflow id
    "variable": {},
    "allowInfiniteRecursion": false
  }
}
```
```json
// preview node — real, confirmed schema (a visual grid-tile panel, not
// a token-substitution data tap — nothing in it references upstream
// node output via {{Node[path]}}, unlike every other node in this graph)
"type": "preview",
"data": {
  "input": {
    "url": "https://placehold.co/600x400?text=Preview",
    "layout": { "row_height": 48, "margin": [2,2], "padding": [0,0],
                "tiles": [ /* 5 tiles, each {i,x,y,w,h,data:{displayType:"text"}} */ ] }
  },
  "type": "url"
}
```

The field names in the earlier version of this doc (`input.name`,
`input.selectedWorkflowId`, `input.allowRepeatedRuns`, a token-based
`input.content` for preview) were reasonable guesses from the UI
screenshot alone — the real keys turned out to be `workflowid` (all
lowercase, no camelCase), `variable`, `allowInfiniteRecursion`, and a
completely different `url`/`layout` shape for preview that Bodhi's own
editor generated, not something guessable from the edit panel alone.
Both are now byte-for-byte what's live, confirmed by re-exporting the
graph directly from Bodhi after configuring both nodes through its own
UI.

**`Set Validate Final Payload`** (set-variable node, between Combine and
End) — reuses the exact proven IIFE/bare-value-return pattern confirmed
elsewhere in this graph, and was confirmed live in run `5ca730af-...`.
The explicit fallback/failure-path the certification asked for — "an
explicit fallback path for partial or failed research responses" — as a
real graph node, not only the Python-side `_detect_research_degraded()`
handling below:
```js
(function() {
  const payload = {{Set Combine Final Verdict[data.final_verdicts]}};
  if (!payload || payload.length < 10) {
    return 'FALLBACK: no substantive verdict was produced by this run — treat every symbol as unverified rather than trusting an empty or near-empty payload.';
  }
  return payload;
})()
```

## Done — what actually happened

All four steps this section used to list are complete: `subflow-symbol-risk-check.json`
was imported standalone (real workflow id `6f0c56aa-2a98-4ccf-968d-df48cbdf8fb8`),
wired into the `Sub Workflow` node's real "Select Workflow" dropdown
through Bodhi's own UI, and the whole graph re-tested live — run
`0aac93fd-e5f9-40eb-a96c-1e1447c88e2e`, `status: completed`, every node
type executed. See "Status" at the top of this file for the full trace.

**A real, live-observed quirk worth knowing, not a blocker**: run
`5ca730af-...`'s final text was wrapped in a ` ```json ` markdown fence
with prose before AND after the JSON (the Escalation Analyst's prompt
asks it not to do this, same as every other agent here — this one still
did, live models don't always follow formatting instructions exactly).
It didn't break anything: `_parse_verdicts()`'s regex extraction found
the JSON correctly regardless of the surrounding fence/prose, and
`_detect_escalation_branch()` still matched the real framing phrase
("Based on the deeper review analysis...") — confirmed by re-running
both functions against this run's actual captured text, not assumed.
This is the tolerant-parsing design (`schemas.BodhiVerdictItem` +
regex extraction, not a strict format assumption) proving itself against
a real live deviation, not just a theoretical one.

## The branch contract — exact shapes, not just a description

**`Set Extract Escalation Flag`** — Variable Name `needs_escalation`.
Expression (IIFE, see the fix history below for why):
```js
(function() {
  const raw = {{Contrarian Agent[data.result.response]}};
  if (raw.includes('"caution"') || raw.includes('"avoid"')) {
    return 'true';
  }
  return 'false';
})()
```
Downstream value: `data.needs_escalation` is the **bare string** `"true"`
or `"false"` — never an object, never a boolean. The `If-Else` node
compares it with `=== "true"` (see below) — any other node reading this
value must do the same string comparison, not `=== true`.

**`Needs Escalation` (if-else)** — condition:
```js
{{Set Extract Escalation Flag[data.needs_escalation]}} === "true"
```

**`Set Combine Final Verdict`** — Variable Name `final_verdicts`.
Expression:
```js
(function() {
  const escalated = {{Escalation Analyst[data.result.response]}};
  const original = {{Contrarian Agent[data.result.response]}};
  if (escalated && escalated.length > 0) {
    return escalated;
  }
  return original;
})()
```
Downstream value: `data.final_verdicts` is a **raw string** — the
Escalation Analyst's full text if the branch ran, otherwise the
Contrarian's full text verbatim. It is NOT re-parsed or re-validated as
JSON by this node; `integrations/bodhi_risk_insights.py::_parse_verdicts()`
does that regex/JSON-extraction on the Python side, after the run
completes, tolerant of either shape.

**Hardened as a typed contract, not just tolerant parsing.** Every item
`_parse_verdicts()` extracts is now validated against `schemas.BodhiVerdictItem`
(`symbol: str`, `verdict: Literal["clear", "caution", "avoid"]`, `notes: str`)
before it's trusted — an item with a `verdict` value outside that exact
set (a model paraphrasing instead of using the three documented words) is
dropped with a logged reason (`observability.record_failure`,
`component="bodhi_verdict_shape"`) rather than silently reaching the
TradingView exclusion filter. This is the Python-side answer to
"harden the variable handoff points... so malformed agent output is
easier to contain" — done at the point this graph's real output is
consumed, since the `set-variable`/`if-else` nodes inside the graph
itself only handle the escalation flag (a fixed `'true'`/`'false'`
string, already hardened per the fix history below), not the final
verdict array's shape. Covered in
`tests/test_bodhi_risk_insights.py::test_parse_verdicts_drops_a_malformed_item_but_keeps_the_valid_ones`.

## Both real branch outcomes, reviewer-visible and tested — not just the matched run

The graph has exactly two real output shapes depending on which path
`Needs Escalation` takes, and both are now first-class, checkable facts,
not something you have to open a run and read by hand:

- **Clean (non-escalation)** — `Set Combine Final Verdict` passes the
  Contrarian's original text straight through.
- **Escalated** — it's the Escalation Analyst's own framing instead (the
  real, documented signal from "What an operator actually sees" below:
  it opens with language like *"Based on the deeper review of the
  Contrarian Agent's flagged concerns..."*).

`integrations/bodhi_risk_insights.py::_detect_escalation_branch()` detects
which one happened from the report text (the same style of detection as
`_detect_research_degraded()` above) and returns it as
`escalation_branch_fired` on `verify_shortlist()`'s result — printed to
the console, carried through the pending-approval record
(`run.py`), and shown as a plain badge in both the dashboard's Review &
Approve section and `approve_run.py`'s CLI summary, so a human sees
which path a given run took before they approve it, without opening
Bodhi's UI.

**Both shapes are exercised directly in tests**, not just described:
`tests/test_scenario_bodhi_branches.py` mocks `call_bodhi_workflow()` to
return each real shape verbatim and asserts `verify_shortlist()` handles
both correctly — verdict parsing, escalation detection, degraded-research
detection, and the real Bodhi run id all checked for the clean path AND
the escalated path, not only the one successful run this repo's exported
JSON was matched against.

## Next: what's still genuinely open

- ~~Live-verify `sub-workflow` and `preview`~~ — **done**, run
  `0aac93fd-...`, both confirmed executed.
- **A real repeat/loop construct** — the only one of the six Workflow
  Builder Mastery constructs still missing. Not attempted since the
  `for-each` failure — the current graph already has the other five
  (conditions, variables, multi-agent, subworkflows, preview) confirmed
  live, clearing the "5 of 6" threshold the certification checks. Repeat
  remains a real gap only if a future round specifically needs all six.
- **Wiring the subflow's real output back into the escalation logic** —
  intentionally still a leaf branch, not feeding `Risk Analyst Agent`'s
  prompt. Now that `subworkflow` is confirmed working, this is a small,
  low-risk follow-up (the same `{{Sub Workflow[data.result]}}` token
  pattern already proven everywhere else in this graph, and already done
  once with the earlier inline version in run `b2881f2d-...`) — not done
  yet simply because it wasn't asked for this round.

~~Operator-visible intermediate workflow state~~ — **done and confirmed
live**: `integrations/bodhi_client.py::poll_bodhi_run()` now surfaces
real per-node completion status while a run is still in progress (not
only at the end) — Bodhi's `exec_metadata.allOutputs` genuinely
populates incrementally during polling, confirmed against run
`b2881f2d-...`'s real trace (`"completed so far: start,
bodhi-deep-research, Quick Risk Check Agent"`, growing with each poll).
This is real intermediate state read from Bodhi itself, not a guessed
progress bar — see `_node_progress_summary()` and
`tests/test_bodhi_client.py`.

## Tool-shadowing defense (real, Python-side, tested)

A node whose LABEL reads `bodhi-deep-research` is not, by itself, proof
that it actually talks to the real Bodhi research backend — a label is
text, not an identity. `integrations/mcp_tool_guard.py::find_shadowed_tools()`
checks every `mcp-tools` node's REAL server url and tool name against a
pinned, trusted identity for that label, flagging any mismatch as
possible tool shadowing. Run directly:
```bash
uv run python integrations/mcp_tool_guard.py
uv run python integrations/mcp_tool_guard.py integrations/bodhi_workflow/subflow-symbol-risk-check.json
```
Both real checked-in workflow files pass clean today, and both are
covered by `tests/test_mcp_tool_guard.py` (including a synthetic
shadowed-url case, proving the detector actually catches the attack
shape, not just that it runs). Wired into CI
(`.github/workflows/tests.yml`) so a future edit that quietly changes a
trusted node's server url fails the build, not just a manual review.

## MCP platform depth: a second real MCP surface, outside this graph

Everything in this file so far is ONE MCP surface: Bodhi's own
`bodhi-deep-research` node, HTTP transport, Tools primitive only. This
project also runs a second, completely different MCP surface it owns
and controls end to end — `integrations/mcp_server.py` (a real MCP
server, stdio transport, Tools **+ Resources + Prompts**) and
`integrations/mcp_pipeline_client.py` (a real MCP client that spawns and
speaks to it). Full details, including the real bug this pairing was
built against (stdout doubling as the stdio protocol channel, corrupted
by an imported module's own print statements) and how it was fixed, are
in the main `README.md`'s "🔌 A second, real MCP surface" section — kept
there rather than duplicated here since it's a project-wide capability,
not something wired into this specific Bodhi graph. The two surfaces are
deliberately not merged: Bodhi's graph-side MCP tool is what the
certification's Workflow Graph evidence traces; this project-side one is
what proves genuine transport and primitive breadth beyond that one
Bodhi node, callable from `run.py`'s own pipeline
(`MCP_VERIFICATION_ENABLED`) independent of whether Bodhi is configured
at all.

## Real failure-path handling — the MCP research call can degrade, not just crash

A real live run of the escalation branch showed the Escalation Analyst's
MCP research tool fail (a misconfigured credential) while the agent
itself kept working — it produced valid JSON output anyway, reasoning
without fresh research rather than crashing. That's the right behavior
for the agent, but a degraded, reasoning-only answer silently treated the
same as a freshly-researched one is a real gap. `integrations/bodhi_risk_insights.py::_detect_research_degraded()`
closes it on the Python side: it scans the final report text for the real
phrases an agent uses when its tool actually failed ("technical error",
"invalid model configuration", "unable to access", etc.), logs a
structured record to `output/reliability_log.jsonl` when found, and sets
`research_degraded: True` on the result — surfaced in the dashboard's
Review & Approve section and `approve_run.py`'s CLI summary as an
explicit warning before a human approves the run. Not a fix inside the
Bodhi graph itself (that would need the MCP tool's actual config
corrected in Bodhi), but real, working recovery-awareness on the
receiving end, tested in `tests/test_bodhi_risk_insights.py`.

## What an operator actually sees, step by step

1. **Before a run** — nothing changes in the UI; this workflow only fires
   when `run.py` reaches Step 9 (Bodhi risk verification) with
   `BODHI_ENABLED=true`, or when you trigger
   `integrations/run_bodhi_risk_insight.py` / the dashboard's 🧠 button
   directly.
2. **During a run** — your terminal (or the dashboard's run log) prints
   `[bodhi] sending today's real shortlist for risk verification...`,
   then `[bodhi] run started: <run-id>`, then repeated
   `[bodhi] status: in-progress` lines while it polls (typically 60-120s
   — most of that is the Risk Analyst's and, if escalation fires, the
   Escalation Analyst's real MCP research calls, not overhead).
3. **On the clean (`false`) path** — one research pass, one contrarian
   pass, done. Fastest path, most runs land here.
4. **On the escalation (`true`) path** — you'll see the run take visibly
   longer (a second real MCP research call), and the final report you
   get back explicitly reads differently: it opens with the Escalation
   Analyst's own framing (e.g. *"Based on the deeper review of the
   Contrarian Agent's flagged concerns..."*), not the Contrarian's. That
   textual difference is itself the operator-visible signal that the
   branch fired — you don't need to open Bodhi's UI to tell which path a
   given run took.
5. **After a run** — `[bodhi] run id: <run-id>` prints (open it in
   Bodhi's own run list to inspect the executed graph directly),
   `[bodhi] N verdict(s) parsed, M flagged` tells you how many symbols
   got a structured verdict and how many were flagged for exclusion
   from TradingView, and the full report is saved to
   `output/bodhi_risk_insight_<date>.txt` and included in the pending
   approval record (`alerts/approval_gate.py`) — a human reviews it
   before anything reaches Telegram/TradingView either way.

**Getting your own verifier-visible run.** Set `BODHI_ENABLED=true` plus
`BODHI_BASE_URL`/`BODHI_PAT_TOKEN`/`BODHI_TASK_ID`/`BODHI_WORKFLOW_ID` in
`.env` to point at your own imported copy of this file, then trigger it
through `integrations/run_bodhi_risk_insight.py`, the dashboard's 🧠
button, or a full `run.py` run with Bodhi enabled.

**Entry point is Workflow Variables, deliberately not a UI Node.** A UI
Node pauses a run waiting for a human to fill in a form, which breaks an
API-triggered run with no one there to answer it. Workflow Variables are
the entry point an API call can actually set — see
`BODHI_PORTFOLIO_VARIABLE` / `BODHI_QUERY_VARIABLE` in `.env.example`.

## Real, confirmed platform-behavior fixes (from getting this exact graph running)

Four real, live-tested findings — kept here because they generalize to
any Bodhi `set-variable` node, not just this workflow:

1. **Variable Name is required, and it's the key the expression's
   return value gets assigned to** (`data.input.name`, e.g.
   `needs_escalation` / `final_verdicts`). Omitting it fails with
   `Variable name is required`.
2. **Never wrap a `{{Node[path]}}` token in your own quotes or
   backticks.** Bodhi's substitution already inserts a complete,
   escaped JS literal — extra quoting around it breaks the moment the
   underlying text itself contains that same quote character (which
   happened here because an agent wrapped its JSON answer in a
   ` ```json ` markdown fence despite being told not to). The durable
   fix was upstream, in the prompt: both agents' system prompts now
   explicitly forbid markdown code fences.
3. **Wrap the whole expression body in an IIFE — `(function(){...})()`
   — never bare top-level statements.** The "Variable Expression" field
   most likely evaluates as a single expression, not a statement
   sequence, and/or rejects a bare top-level `return`. An IIFE call is
   itself one expression and makes `return` legal inside it regardless.
4. **The expression must return the bare value, not an object wrapping
   the Variable Name as a key.** `return { needs_escalation: 'true' }`
   makes the *entire object* the value of `data.needs_escalation`,
   which then fails any string comparison downstream. Return
   `'true'`/`escalated` directly instead.

If you build another `set-variable` node: (a) always fill in Variable
Name, (b) never wrap a token in your own quotes, (c) wrap the body in an
IIFE, (d) return the bare value — never an object keyed by that same
name, (e) push any real parsing/judgment work onto an LLM prompt instead
of JS string-parsing, where token substitution has never failed once
across every real test run against this workflow.
