# Bodhi workflow — files in this folder

| File | What it is | Confidence |
|---|---|---|
| `workflow-original-confirmed.json` | 5 nodes: Start → Risk Analyst (+MCP) → Contrarian → End. **Actually run end-to-end against a live Bodhi instance.** Kept as a known-good rollback point. | ✅ Verified |
| `workflow-complete-with-telegram.json` | **16 nodes. The current file — three real runs tested against live Bodhi.** UI Node (capital, stock count, universe, risk question) → auto-screening + shortlisting → whole-shortlist risk review (merged research + self-challenge) → escalation/data-insufficient branching → Telegram. | ⚠️ Mostly confirmed — read below |
| `MANUAL_BUILD_GUIDE.md` | Click-by-click manual steps for building a version of this by hand in Bodhi's canvas — written before the scope-down below, so it still describes the larger 49-node design. Useful for the node-by-node mechanics; the shape has since been trimmed. | ✅ Mechanics confirmed |
| `RUN_EVIDENCE.md` | **Generated, never hand-edited.** Real Bodhi run ids this app has actually captured, built from `output/metrics.jsonl`. | See "Execution evidence" below |
| `collect_run_evidence.py` | Regenerates `RUN_EVIDENCE.md` from real telemetry — `uv run python integrations/bodhi_workflow/collect_run_evidence.py`, or the dashboard's 🧾 button. | — |
| `workflow-AI-Equity-Analyst-846bb836-...json` | Retired 20-node dynamic-loop design (never confirmed to import). No longer maintained — see "History" at the bottom of this file. | ⚠️ Superseded |

## ⚠️ Not a drop-in replacement for what the local app calls today

`workflow-complete-with-telegram.json` takes its real inputs on a **UI
Node** (`Total Capital`, `Number of Stocks`, `Universe`, `Risk Question` —
its Start Node only carries an unused placeholder variable, matching the
reference export's own convention). `integrations/bodhi_risk_insights.py`
/ `.env`'s `BODHI_PORTFOLIO_VARIABLE`/`BODHI_QUERY_VARIABLE` are unaware of
the UI Node — that integration code has intentionally **not been
changed** here (no `.py` files were touched building this). Run this
workflow independently via Bodhi's own UI/API as a standalone
demonstration, or say the word and I'll wire the local app to call it
instead.

## Round 4 — auto-screening added back, and three more real bugs fixed

A third live run (portfolio typed in by hand, since screening had just
been removed) got further than any previous run — all the way to a
`success` status on every node, including `End`. But the actual data was
wrong: `final_output` came back `null`, and Delivery Agent's own reply
showed the Telegram tool erroring with `"inputString.match is not a
function"`. Digging into the real output JSON found three independent
bugs, plus you asked for auto-screening (capital / stock count / universe
dropdown, scan the index, shortlist N stocks) to be added back — done
together, since fixing the pipeline and reintroducing screening touch the
same nodes.

**Bug 1 — every Set Variable reference used the wrong path.** Compare the
real, confirmed output shapes from that run:
```
UI Node's real shape:            data.response["FieldName"]   (matches what was written)
Set Variable's real shape:       data.<varName>                (FLAT — no ".response"!)
```
Every reference to a Set Variable node's output — `{{Set Combine
Decisions[data.response["decisions_combined"]]}}` and similarly for
`final_output` — was wrong; that path doesn't exist on a Set Variable
node, so it resolved to nothing. That's the entire reason `final_output`
was `null`, which is also why Delivery Agent had nothing real to send and
the Telegram tool choked on non-string input. **Fixed everywhere** — all
references now use the flat `data.<varName>` form, and each Set Variable
node's own declared `path` matches.

**Bug 2 — Escalation Analyst wrapped its reply in an extra key.** Asked
for `{"decisions": [...]}`, it actually replied
`{"escalated_review_decision": {"decisions": [...], ...extra fields...}}`.
Harmless to `JSON.parse()` itself, but it meant `.decisions` didn't exist
at the top level where the next node expected it. **Fixed** by adding an
explicit "no wrapper key" instruction to the shared robustness clause
every agent gets, with a concrete negative example.

**Bug 3 — `Set Combine Decisions` concatenated all three sources instead
of picking the one that won.** The real run proved something important:
Risk Reviewer Agent always executes (it's the first node, never gated
behind an If-Else), so it's never `null` — and the previous
`.filter(Boolean)` logic kept it *alongside* whichever branch fired after
it, producing 2 decision-objects in the combined array on the escalation
path instead of 1. **Fixed** by switching from "keep every non-null
source" to a priority-fallback pick: `Escalation || Data Quality || Risk
Reviewer`, parsed once, `.decisions` extracted — the first truthy raw
response wins, matching how the branches are actually meant to be
mutually exclusive outcomes for the same review.

**What you asked for, added back cleanly:** the UI Node now collects
`Total Capital`, `Number of Stocks`, and `Universe` (NIFTY50/NIFTY500 —
as a text field, since a confirmed dropdown/select field type hasn't been
seen; check if "Add Field" offers one when you build this and switch to
it if so) instead of a hand-typed portfolio JSON blob. A new **Scout
Decision Agent** (+ its own MCP tool — 2 nodes total, not the old 9-node
fan-out) screens the chosen index and produces the shortlist in one call,
combining what used to be two separate local-app agents (the Scout and
the Decision Agent) into one, matching the project's own "minimize AI
calls" discipline. `Set Combine Final Output` now also computes an equal
capital split via plain JS arithmetic (`Total Capital / number of picks`)
— never asking any LLM to do the money math, matching the local app's own
stated principle.

**Net node count: 16** (14 + Scout Decision Agent + its MCP tool) — still
close to the requested 14-15, the 2-node increase being exactly the
screening capability you asked to have back.

## `workflow-complete-with-telegram.json` — 16 nodes

```
Start (placeholder var)
  → UI Node (Total Capital, Number of Stocks, Universe, Risk Question)
  → Scout Decision Agent ◄MCP (screens the index, produces the shortlist, one call)
  → Risk Reviewer Agent ◄MCP (research + self-challenge, ALL shortlisted stocks in one call)
        → If-Else: Review Needed?
              true  → Escalation Analyst ◄MCP (deeper pass, only on blocked stocks)
              false → If-Else: Data Insufficient?
                          true  → Data Quality Handler (no MCP — honest "not reviewed")
                          false → (Risk Reviewer's own decisions stand — CLEAR)
        → Set Combine Decisions (picks whichever of the 3 paths actually decided, priority order)
  → Set Combine Final Output (+ plain-arithmetic capital split, no LLM math)
  → Delivery Agent ◄HTTP Tool (Telegram sendMessage)
  → End
```

### Why 16 nodes, not 49 and not a bare 14

A second live run showed that removing screening entirely (down to 14
nodes) traded away something real: with no auto-discovery step, the human
had to hand-type a portfolio, which isn't what "scan NIFTY50/500 and
shortlist N stocks" actually needs. So screening is back — but as **one
agent + one MCP tool** (`Scout Decision Agent`), not the earlier 9-node
fan-out (a separate Scout, then 3 duplicated If-Else-gated Quant Scorer
branches, then a separate Decision Agent). The reasoning for keeping it
lean still holds:

- **The screening stage depends on the exact same
  `bodhi-deep-research` MCP tool** that's been failing in this workspace
  across all three runs so far — see "Worth checking on the platform
  side" below, now with a confirmed exact error message. More nodes
  calling the same tool add more places to hit the same failure, not more
  coverage.
- **Screening failing gracefully no longer starves the whole graph.**
  If Scout Decision Agent's research tool call fails, its own prompt has
  it return `[]` rather than a fabricated pick — and unlike the earlier
  49-node design, there's no per-slot exists-check gating everything
  downstream on Scout having found something. Risk Reviewer Agent still
  runs on whatever it got (even an empty list), and a genuine tool
  failure at that stage routes to the `Data Insufficient` branch — a
  real, reachable path to `End` and a real Telegram message, not a
  silent cascade of "Upstream branch not taken."
- Combining what the local app does as two separate agents (Scout, then
  Decision Agent) into one `Scout Decision Agent` call matches the
  project's own stated cost discipline — batch aggressively, don't spend
  an extra AI call where one can do both jobs.

If you want the fuller, more granular screening pipeline back (separate
Scout + per-stock Quant Scorer fan-out) — once the MCP tool issue is
resolved on the platform side — `MANUAL_BUILD_GUIDE.md` still has the
full node-by-node mechanics for it; nothing about that design was wrong,
it just wasn't provable in this environment and added little beyond what
one combined agent now covers.

### Bug history — four real rounds of live testing, real fixes each time

**Round 1 — label and templating bugs**, found importing the first
version:
1. Node labels can't contain special characters (`?`, `:`, `(`, `)`, `/`,
   `·` all confirmed to trigger "Label cannot contain special
   characters"). Every label in this file uses plain alphanumeric +
   spaces only.
2. A brace-escaping bug in how prompts were generated wrapped JSON reply
   examples in double curly braces instead of single ones (`{{"symbol":
   ...}}` instead of `{"symbol": ...}`) — Bodhi reads any `{{...}}` as a
   node reference and threw "Node ... does not exist" trying to resolve
   the JSON text itself as a node name. Fixed in every agent prompt.

**Round 2 — cascading failure from ungraceful agent behavior**, found
from a real run where the MCP tool failed:
1. Every agent's prompt now ends with an unconditional rule: no matter
   how invalid, placeholder, or nonsensical the input looks, always reply
   with the required JSON schema — never a conversational reply, never a
   clarifying question. (A live run showed an agent literally ask "which
   stock would you like me to analyze?" in prose when given garbage
   input, which broke the JSON.parse() downstream of it.)
2. Both JSON-combining Set Variable expressions wrap their `JSON.parse()`
   calls in try/catch, so one malformed entry gets dropped instead of
   throwing and cascading "Unreachable" to every remaining node.

**Round 3 — scoped screening out temporarily.** A second real run (after
round 2's fixes) correctly produced `[]` from Scout Agent under the same
broken tool, and everything downstream correctly self-skipped rather than
crashing — but "correctly skips everything" still isn't the same as
"provably works end to end." Screening was removed for one round while
the rest of the pipeline was hardened.

**Round 4 — screening added back (see above), plus three more real
bugs**, found from a third live run that reached `End` with `status:
"success"` everywhere but wrong data:
1. Every Set Variable reference used the wrong path
   (`data.response["var"]` instead of the confirmed-real flat
   `data.var`) — this is what made `final_output` come back `null`.
2. Escalation Analyst wrapped its reply in an extra top-level key instead
   of the bare schema asked for.
3. `Set Combine Decisions` concatenated all three possible sources
   instead of picking the one that actually won, because Risk Reviewer
   Agent (unlike Escalation Analyst / Data Quality Handler) always
   executes and so was never `null`.

All three fixed — see "Round 4" section above for the detail on each.

### What's confirmed vs. still worth testing

| Confidence | What |
|---|---|
| ✅ Confirmed from real runs | `control-start`, `ui`, `agent-custom`, `mcp-tools`, `if-else` (JS `condition`, `true`/`false` handles) all executed correctly across 3 runs. A reference to a node whose branch never fired resolves to `null` in an expression — confirmed, and now correctly used as a `||` priority-fallback rather than a filter-and-keep-all. `{{...}}` substitution is always a properly-escaped JS string literal, which is why `JSON.parse({{Ref}})` works — but a Set Variable node's own output must be referenced via the flat `data.<varName>` path, not `data.response[...]` (that's UI-Node-only). |
| ⚠️ Imported cleanly, HTTP call still unconfirmed | `http-tool` (Telegram Send Tool) — registers successfully every run, but its one real invocation attempt threw `"inputString.match is not a function"`, most likely a symptom of receiving `null`/non-string `text` (now fixed upstream) rather than a config problem with the tool itself. Re-test now that `final_output` should carry real text. Also still unconfirmed: whether the edit modal has a Body field below what's visible in the original screenshot. |
| ⚠️ Newly rewritten, not yet re-tested | `Set Combine Decisions`'s priority-fallback expression and `Set Combine Final Output`'s arrow-function-with-parameter expression (`(d => {...})(JSON.parse(...))`) — both fixed against confirmed real bugs, but neither has been exercised in a live run since the fix. Test all three routing outcomes, and check the capital-split arithmetic comes out sensible. |

### Worth checking on the platform side, not fixable in this JSON

**Now confirmed, not just hypothesized:** the third live run's own error
text read `"Invalid model name 'gpt-4.1'"` — directly confirming the
`deep_research` tool's default `planner_model`/`writer_model` (`gpt-4.1`,
OpenAI) isn't available in this workspace, while the agent nodes'
own model (`claude-sonnet-4-5`) works fine every time. This needs a fix on
the Bodhi/MCP tool config side — point `planner_model`/`writer_model` at
a model this workspace actually has, or get OpenAI access provisioned for
this tool. No workflow redesign here fixes this; it's the same root cause
behind every "research unavailable" result seen so far.

### Testing order

1. Start → UI Node → Scout Decision Agent → its MCP tool — confirm a real
   (or honestly empty) shortlist comes back.
2. → Risk Reviewer Agent → its MCP tool.
3. Force each of the three routing outcomes once — a shortlist entry that
   should get flagged (Escalation Analyst branch), a working run that
   should clear cleanly (straight to Set Combine Decisions), and (if you
   can trigger it) a genuine tool failure (Data Quality Handler branch).
   Confirm `Set Combine Decisions` ends up with exactly one decision per
   stock, not duplicates.
4. `Set Combine Decisions` → `Set Combine Final Output` — check
   `final_output` is real JSON, not `null`, and the `allocation_note`'s
   arithmetic looks right.
5. Build/verify the Telegram HTTP Tool, wire Delivery Agent, confirm a
   real message arrives.
6. → End.

## Legacy: the earlier complete workflow

```
Start (Universe, InvestorProfile, RiskQuestion)
  │
  ├─► Scout Agent ◄── MCP: bodhi-deep-research
  │     └── screens the universe, replies as a BARE JSON watchlist array
  │
  └─► Repeat · watchlist ──────────────────────────────────────┐
        │                                                      │
        ├─► Quant Scorer Agent ◄── MCP: bodhi-deep-research    │
        │     └── fundamental + technical + risk scores, one call/stock
        └─► Set · append analysis ─────────────────────────────┘
                                        │
                                        └── loops back to Repeat
  │
  ├─► News/Event Reader Agent ◄── MCP: bodhi-deep-research (ONE call, whole watchlist)
  ├─► Decision Agent  (no MCP — pure synthesis)
  │     └── replies as a BARE JSON shortlist array
  │
  └─► Repeat · risk review ─────────────────────────────────────┐
        │                                                       │
        ├─► Risk Reviewer Agent ◄── MCP: bodhi-deep-research    │
        │     └── research + self-challenge, closes with        │
        │         decisions JSON + ROUTING_FLAG                 │
        │                                                       │
        └─► Switch · review outcome                             │
              ├── review_needed     ─► Escalation Analyst ◄── MCP (deeper)
              ├── data_insufficient ─► Data Quality Handler
              └── clear             ─────────────────────┐      │
                 all three converge ─► Set · append decision ───┘
                                        └── loops back to Repeat
  │
  └─► (loop done) ─► Set · combine final output ─► End ({shortlist, risk_decisions})
```

**No decorative nodes.** Every node either does real work (an agent call, a
tool call, a branch decision) or holds state something else actually
reads. An earlier draft had `Set · current symbol` / `Set · research
status` / `Preview` nodes that wrote variables nothing downstream ever
read — removed, and the graph rewired directly around them.

## Risk Analyst + Contrarian, merged into one Risk Reviewer Agent

The former two-agent risk-review design (a Risk Analyst researching, then
a separate Contrarian agent challenging it in a fresh context) is now
**one agent**, structured as two explicit steps in a single prompt: STEP 1
researches; STEP 2 argues against its own STEP 1 findings before
committing to a decision. Same output contract as before (`decisions`
JSON + `ROUTING_FLAG` line), one fewer node.

**The trade-off, stated plainly, not hidden:** a genuinely separate second
agent, with no memory of writing the first assessment, is structurally
better at actually overturning a bad initial call than the same agent
critiquing itself in one continuous context — self-critique is a real,
useful pattern, but it's not the same strength of independent check.
`PROJECT_OVERVIEW.md` specifically credits the two-agent design with
"seen overruling" the first assessment in testing; whether the merged
single agent does that as reliably is worth watching once you have real
runs to look at (`RUN_EVIDENCE.md` + the `reason` field on each decision).
If it turns out to matter, this is a one-node change to split back apart.

## What each construct is doing, and why

- **Repeat (×2)** — the watchlist loop and the risk-review loop both live
  *in the platform*. Sequential, not nested, so `{{$item[...]}}` is
  unambiguous at every point despite both loops reusing the same alias.
  `continueOnError: true` on both — one stock failing doesn't abandon the
  rest.
- **Bare-JSON discipline** — Scout Agent and Decision Agent reply with
  **only** a JSON array, nothing else, because their output feeds a
  Repeat node's `items` directly. The Risk Reviewer/Escalation/Data-Quality
  agents instead close with JSON *plus* a routing line, because those feed
  a Switch's substring match (tolerant of trailing text) — a deliberately
  different consumption pattern for a deliberately different node type.
- **Switch (3-way)** — not just "escalate or don't". `data_insufficient`
  is a genuinely different situation from "we looked and found a
  problem", and is the Switch's `default` — a malformed reply can never
  be silently read as "clear".
- **Set · append analysis / append decision / combine final output** — the
  three real accumulators in this graph. Each is read by something
  specific downstream (Decision Agent, End, End) — nothing here is
  write-only.
- **Data Quality Handler** — the failure branch returns the *same*
  decision schema as every success branch, explicitly forbidden from
  reviewing the stock from general knowledge — an honest "not reviewed"
  record, not a guess dressed up as a review.
- **Explicit contracts** — every node carries a `_contract` field in its
  `data` stating inputs, outputs and failure behaviour, verifiable from
  the export alone.

## The mapping — every real pipeline stage, and what it became in Bodhi

| Real project (Python) | Bodhi node(s) | What changed |
|---|---|---|
| `data/nse_universe.py` + `data/market_data.py` + `agents/research_agent.py` (the Scout's anomaly score) | **Scout Agent** + MCP | The real Scout is a deterministic formula over real Yahoo Finance data. Bodhi has no exchange-data API, so this became an **AI research approximation** — the prompt describes the exact formula and asks the agent to find real, current candidates matching that profile via web research. Numeric precision traded for architectural completeness, flagged explicitly. |
| `agents/fundamental_agent.py` + `technical_agent.py` + `risk_agent.py` (3 zero-AI formula agents) | **Quant Scorer Agent** (one node, one MCP call/stock) | Combined into one agent scoring all three dimensions — more faithful to the project's own "batch aggressively" cost discipline than three separate calls, at the cost of the real formulas' exactness. The literal rubric from all three source files is in the prompt. |
| `agents/sentiment_agent.py` (News/Event Reader, 1 batched call) | **News/Event Reader Agent** | 1-to-1: one call for the whole watchlist, same output schema. |
| `agents/decision_agent.py` (Decision Agent, 1 batched call, no new data) | **Decision Agent** (no MCP) | 1-to-1: same inputs, same output schema, still not asked to compute share quantities or capital split. |
| `integrations/bodhi_risk_insights.py`'s risk-review design | **Risk Reviewer Agent → Switch → Escalation Analyst / Data Quality Handler** | Risk Analyst + Contrarian merged into one agent (see above); everything else carried over. |

## What is architecturally impossible inside Bodhi, and stays local

Bodhi is a cloud platform. These are not "harder to build" — they cannot
run there at all, regardless of node schema:

- **TradingView push** (`alerts/tradingview_watchlist.py`) — drives a
  Chrome DevTools Protocol connection to *your own machine's*
  already-logged-in TradingView Desktop app on `localhost`. No
  `localhost` exists from Bodhi's side.
- **Excel tracker** (`alerts/excel_report.py`, `tracker/price_tracker.py`)
  — writes to a file on your machine. Same reason.
- **Knowledge base / calibration feedback loop**
  (`memory/knowledge_base.py`, `tracker/quality_report.py`) — reads a
  local ChromaDB store built from your own run history.

**Telegram** (`alerts/telegram_alert.py`) is the one exception worth
naming: its Bot API is a public REST endpoint an HTTP Request node *could*
call directly. Deliberately **not included** — no confirmed schema for
Bodhi's HTTP Request node, and more importantly, wiring delivery straight
into the workflow would bypass this project's human approval gate
(`alerts/approval_queue.py`). The intended pattern: Bodhi hands back
`{shortlist, risk_decisions}`, a human reviews it, *then* something sends.

## Branch conditions — exactly what routes where

The Switch node evaluates the Risk Reviewer Agent's raw output text
against three literal substrings, in this priority order:

| Branch | Trigger (substring match) | Destination | Meaning |
|---|---|---|---|
| `review_needed` | `ROUTING_FLAG: REVIEW_NEEDED` | Escalation Analyst → its own MCP → append | Decision was `"blocked"` |
| `data_insufficient` | `ROUTING_FLAG: DATA_INSUFFICIENT` | Data Quality Handler → append | Step 1 reported `RESEARCH_STATUS: UNAVAILABLE` |
| `clear` | `ROUTING_FLAG: CLEAR` | append directly | Decision was `"proceed"`, research was usable |
| *(no match)* | anything else, or a missing flag | falls to `data_insufficient` (the `default`) | A malformed reply is never read as "clear" |

The Risk Reviewer Agent's prompt is the single place that writes this
flag, required to emit **exactly one** `ROUTING_FLAG:` line as the last
line of its answer.

## MCP step contract and reviewer handoff

**bodhi-deep-research (four instances — scout, quant, news, first-pass risk, escalation):**

| | Value |
|---|---|
| Input | `{"topic": string, "search_api"?: string, "max_search_depth": int, "number_of_queries": int}` |
| Output | `{"result": string}` — free-text research report |
| Failure mode | Empty/error result → the calling agent MUST say so explicitly (`RESEARCH_STATUS: UNAVAILABLE`, or a notes field saying data wasn't found) — never fabricate findings to fill the gap |

**Reviewer handoff:**

| Agent | Receives | Must produce |
|---|---|---|
| Scout Agent | `Universe` | bare JSON watchlist array |
| Quant Scorer Agent | `{{$item[stock]}}` | JSON: fundamental/technical/risk scores + notes |
| News/Event Reader Agent | `Analyses_accumulator` (whole watchlist) | JSON news array |
| Decision Agent | `Analyses_accumulator`, News output, `InvestorProfile` | bare JSON shortlist array |
| Risk Reviewer Agent | `{{$item[stock]}}`, `RiskQuestion` | research + self-challenge prose, `decisions` JSON, `ROUTING_FLAG` |
| Escalation Analyst | Risk Reviewer's full output, `{{$item[stock]}}` | `decisions` JSON (same schema, deeper research) |
| Data Quality Handler | Risk Reviewer's output only (explicitly NOT asked to review) | `decisions` JSON, `"decision": "blocked"` — an honest "not reviewed" record |

## ⚠️ What is inferred, and what to do about it

Confirmed against real exports: `control-start`, `control-end`,
`agent-custom`, `mcp-tools`. Everything built from those is solid.

**Inferred from naming conventions, not confirmed** — marked
`_UNVERIFIED` in the JSON:

| Node | Guessed type | Also guessed |
|---|---|---|
| Repeat (×2) | `logic-repeat` | `items`/`itemAlias`, `loop`/`done` handles, `{{$item[...]}}` |
| Switch | `logic-switch` | `cases[]`, branch handles named by `key` |
| Set Variable (×3) | `logic-set-variable` | `assignments[]`, `operation: "append"`, and one that assumes `value` supports the same `{{...}}` templating as agent prompts, to combine two JSON blobs |
| Append step | — | `{{$branchResult[...]}}` — a "whichever branch ran" reference that may not exist |

**Before trusting this file:** import it, open the canvas, and check each
marked node rendered as the intended type with its config intact. If any
node comes in broken, delete it, drag the real one from the palette,
configure it as described here, re-wire, and **export from Bodhi to
replace this file** — the export is the source of truth, not this
hand-authored version.

If `{{$branchResult[...]}}` isn't real, bind the append step to each
incoming branch explicitly instead (three assignments, one per source
node).

## Fallback ladder

If this won't import or behave, step down rather than starting over:

1. **Tier 1 — known good.** Import `workflow-original-confirmed.json`.
   Definitely works. No branching, no screening/scoring — just Risk
   Analyst → Contrarian → End over a whole portfolio blob.
2. **Tier 2 — one inferred node.** From Tier 1, add a single **If-Else**
   after the Contrarian: condition = output contains
   `ROUTING_FLAG: REVIEW_NEEDED`; wire true/false branches to an
   Escalation Analyst / straight to End.
3. **Tier 3 — full.** This file. Test **stage by stage**, not end-to-end
   on the first try:
   1. Start → Scout Agent → Repeat · watchlist (does the bare JSON array
      actually get received and iterated?)
   2. → Quant Scorer → append → loop close
   3. → News Reader → Decision Agent → Repeat · risk review (a second,
      different, smaller array — confirms `$item` scoping is per-loop)
   4. → Risk Reviewer → Switch → the three branches
   5. → Set · combine final output → End (does the two-JSON-blob
      template actually produce valid combined JSON?)

## Execution evidence — a real run id, not a description of one

A design document, however detailed, cannot prove a workflow executed —
only Bodhi's own run history can. Every real call
`integrations/bodhi_risk_insights.py` makes to Bodhi's API returns the run
id Bodhi itself assigns, recorded to `output/metrics.jsonl` on success
**and** on a timeout or error, so even a failed attempt is identifiable
evidence.

**To produce one against the OLD per-symbol calling path**: set real
`BODHI_*` credentials in `.env`, run `uv run python run.py` (or
`integrations/run_bodhi_risk_insight.py`), then regenerate
`RUN_EVIDENCE.md`. Note this still targets whatever workflow
`BODHI_WORKFLOW_ID` points at with the OLD `Investmentportfolio` variable
— see the warning at the top of this file about the variable mismatch
before assuming this exercises the file in this folder.

**To produce one against THIS complete workflow**: trigger it directly
(Bodhi's UI, or a curl matching this file's Start Node variables), then
add the resulting run id to `RUN_EVIDENCE.md` by hand, since it didn't go
through the app's own capture path — until/unless the code is updated to
call it (see the warning above).
