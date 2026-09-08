# Bodhi Workflow Builder — the actual risk-review workflow

`workflow-AI-Equity-Analyst-846bb836-ab0a-4b48-9226-1253617a3699.json` is the **exact** Bodhi Workflow Builder export
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

## risk-review-workflow-with-escalation.json — proposed, NOT yet verified

A second file, `risk-review-workflow-with-escalation.json`, extends the
graph above with a real conditional branch — the exact gap the certifier
has flagged three submissions running ("the workflow remains a straight
hand-off with no explicit branch, repeat, or subworkflow"):

```
start → Risk Analyst Agent ◄MCP → Contrarian Agent
  → Set Extract Escalation Flag  (parses the Contrarian's verdicts JSON,
                                   sets needs_escalation / flagged_symbols
                                   / flagged_notes as real, inspectable
                                   handoff variables — not raw prose)
  → If-Else: Needs Escalation?
        true  → Escalation Analyst ◄MCP (deeper pass, ONLY on the flagged
                 symbols, with the Contrarian's specific reasons in its
                 prompt — real rework, not a duplicate pass)
        false → (Contrarian's original verdict stands — clean exit)
  → Set Combine Final Verdict (escalation's verdict if it ran, else the
    Contrarian's original — a real priority-fallback, not concatenation)
  → End
```

This is **not yet the verified artifact** — `integrations/bodhi_risk_insights.py`
still calls the plain `workflow-AI-Equity-Analyst-846bb836-ab0a-4b48-9226-1253617a3699.json` above. To make this the
new verified workflow: import this file into Bodhi, run it once with a
real portfolio (pick one where at least one symbol should plausibly get
flagged, so the `true` branch actually executes and both paths get real
coverage — not just the `false` path), confirm it completes, then hand
the run ID back so `workflow-AI-Equity-Analyst-846bb836-ab0a-4b48-9226-1253617a3699.json` and
`bodhi_risk_insights.py` can be updated to match, the same way this
project's earlier workflow mismatch got fixed.

**Confidence level, honestly:** `control-start`, `control-end`,
`agent-custom`, `mcp-tools` are the exact same schema as the verified
file above — proven twice. `if-else` confirmed correct on the first
import. `set-variable` needed two rounds of fixes against a real Bodhi
instance, both now confirmed and applied:

1. Bodhi's "Edit Set-Variable Node" panel has a required **Variable
   Name** field (`data.input.name`) the first draft omitted — error was
   `Variable name is required`. Fixed by setting it to the node's
   primary output key (`needs_escalation` / `final_verdicts`); the
   expression's other returned object keys still surface under
   `data.*` regardless of that name.
2. **Bodhi's `{{Node[path]}}` substitution always backtick-wraps the
   injected value internally, regardless of what surrounds the token in
   your source.** The first fix attempt (removing the expression's own
   backticks around the token) did NOT work — a live re-run's error text
   still showed the value arriving wrapped in Bodhi's own backticks. The
   real root cause: the Contrarian/Escalation Analyst agents wrapped
   their JSON answers in a ` ```json ` markdown fence despite being told
   "end your answer with ONLY this JSON" — and since Bodhi's own
   backtick-delimited wrapper can't contain a literal, unescaped
   backtick, the fence's own backtick characters broke the substitution
   *before any of the expression's JS ever ran*. No amount of JS-side
   defensive code can fix this — the break happens at Bodhi's template
   compile step, upstream of the expression executing at all. **The only
   real fix is stopping the agent from emitting the fence in the first
   place**: both agents' system prompts now explicitly forbid markdown
   code fences ("do NOT wrap it in a markdown code fence, do NOT use
   triple backticks anywhere in your answer").

3. **Wrap `set-variable` expressions in an IIFE — `(function() {...})()`
   — don't write bare top-level statements.** Even a maximally simplified
   version (a single `.includes()` check, no regex/try-catch) kept
   failing against four different real payloads with clean, correctly
   escaped substituted content — ruling out data-escaping as the cause.
   The one constant across every failed attempt: multiple top-level JS
   *statements* (`const x = ...; if (...) {...} return ...;`). The
   `if-else` node's single-expression `=== "true"` comparison, by
   contrast, never once failed. That points at a structural mismatch —
   the "Variable Expression" field most likely evaluates as a single
   expression, not a statement sequence, and/or doesn't allow a bare
   top-level `return`. Wrapping the whole body in an immediately-invoked
   function expression sidesteps both possibilities at once: an IIFE
   call is itself one expression, and `return` is legal inside it
   regardless of how the outer field is evaluated. Also simplified to a
   plain `.includes()` substring check (no regex/try-catch/`.map()`) —
   the Escalation Analyst's prompt now reads the Contrarian's full raw
   response directly and finds the flagged symbols itself, rather than
   relying on JS to parse structured data through an uncertain sandbox.

4. **The Variable Name is the key — the expression must return the bare
   value, not an object wrapping that key.** With the IIFE fix in place,
   `Set Extract Escalation Flag` finally executed successfully — but the
   downstream `if-else` node then failed with
   `"{"needs_escalation":"true"}" === "true"`. That proved Bodhi assigns
   whatever the expression returns directly to `data.<VariableName>` —
   returning `{ needs_escalation: 'true' }` made the *entire object*
   the value, which then stringified when compared. Fixed by returning
   the bare value instead: `return 'true';` / `return escalated;`, no
   wrapping object. This also means each `set-variable` node can only
   really carry one clean output value — plan accordingly rather than
   trying to pack multiple named fields into one node's return.

Both fixes are applied in this file's `set-variable` nodes and both
`agent-custom` prompts. If you build another `set-variable` node:
(a) always fill in Variable Name, (b) never wrap a `{{Node[path]}}`
token in your own quotes, (c) wrap the body in `(function(){...})()`,
(d) return the bare value the Variable Name should hold — never an
object keyed by that same name, (e) push any real parsing/judgment work
onto an LLM prompt instead of JS string-parsing, where token
substitution has never failed once across every test run so far.
