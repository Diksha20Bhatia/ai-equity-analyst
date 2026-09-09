# AI Equity Analyst — Architecture

This document supersedes the diagram originally shared for use-case
approval. It covers the same system, updated to what's actually running
today: two more agents in the daily scan (an independent Bull/Bear debate
and a real controller that checks its own work), a second MCP surface
this project owns end to end, a deterministic safety override, and a
human-approval checkpoint before anything is sent anywhere.

For the full plain-English walkthrough (every formula, every file, every
API call), see `HOW_IT_WORKS.md`. For a shorter manager-facing summary,
see `PROJECT_OVERVIEW.md`. This document is the diagram-first reference.

---

## High-level architecture

```mermaid
flowchart TB
    classDef codeOnly fill:#f0f0f0,stroke:#888888,color:#222222
    classDef aiCall fill:#cfe2ff,stroke:#3366cc,color:#003366,font-weight:bold
    classDef router fill:#ffe5cc,stroke:#cc7a00,color:#663300,font-weight:bold
    classDef guardrail fill:#ffd6d6,stroke:#cc0000,color:#660000,font-weight:bold
    classDef bodhiGate fill:#f3e6ff,stroke:#8855cc,color:#4b2a80,stroke-dasharray: 4 3
    classDef humanGate fill:#d6f5f0,stroke:#009980,color:#00594d,font-weight:bold
    classDef output fill:#d9f2d9,stroke:#339933,color:#1a4d1a
    classDef modelBox fill:#fff2cc,stroke:#d6a300,color:#665200,font-weight:bold
    classDef toolBox fill:#eeeeee,stroke:#999999,color:#333333

    YF["Yahoo Finance<br/>prices + fundamentals (plain API)"]
    NSE["NSE Archives<br/>official index list (plain API)"]
    GN["Google News<br/>real headlines (plain API)"]

    subgraph DAILY["DAILY DEEP SCAN — run.py, once a day — 4 AI calls total"]
      direction TB
      A1["1. Scout — scans every stock, flags ~10 acting unusually today"]:::codeOnly
      A2["2. Analyst — is the business financially healthy?"]:::codeOnly
      A3["3. Chart Reader — trend and momentum"]:::codeOnly
      A4["4. Skeptic — how risky/volatile has it been?"]:::codeOnly
      A5["5. News Reader — real headlines, good or bad news · AI CALL"]:::aiCall
      A6["6. Bull Case Agent — strongest HONEST case FOR · AI CALL, parallel"]:::aiCall
      A7["7. Bear Case Agent — strongest HONEST case AGAINST · AI CALL, parallel"]:::aiCall
      A8["8. Decision Manager — adjudicates Bull vs Bear, writes the final pick · AI CALL"]:::aiCall
      A9["9. Orchestrator — checks the Manager's own work, one bounded retry if needed · 0-1 AI CALL"]:::router
      A10["Reliability Guardrail — if two independent tools BOTH fail to confirm a stock is real, force it to 'avoid' — no AI, deterministic"]:::guardrail

      A1 --> A2 & A3 & A4
      A2 & A3 & A4 --> A5
      A5 --> A6 & A7
      A6 & A7 --> A8
      A8 --> A9
      A9 -.->|sends back with specific feedback, max once| A8
      A9 --> A10
    end

    subgraph TOOLS["Real-time verification tools — feed the Decision Manager while it reasons"]
      direction TB
      T1["TradingView — live quote + symbol search"]:::toolBox
      T2["This project's OWN local MCP server — second, independent check + sector notes"]:::toolBox
    end

    subgraph INTRA["LIVE INTRADAY SCANNER — intraday_run.py, market hours only — 1 AI call"]
      direction TB
      B8["Data Sentinel — catches broken/fake-looking data before it's trusted"]:::codeOnly
      B7["Day Trader — mechanically checks for a real breakout happening now"]:::codeOnly
      B9["Narrator — plain-English reason for each top pick · AI CALL"]:::aiCall
      B10["Scorekeeper (optional) — end-of-day report card"]:::codeOnly
      B8 --> B7 --> B9 -.-> B10
    end

    MODEL["Bodhi LLM Gateway — the ONLY AI entry point.<br/>Currently gpt-5, chosen deliberately per agent,<br/>with an ENFORCED spending cap per request."]:::modelBox
    LS["LangSmith (optional) — logs every AI call for audit"]:::toolBox

    SHORTLIST["Shortlist: the number of stocks requested, with reasoning shown"]:::output
    LIVEALERT["Live alerts: breakout picks + plain-English reason"]:::output

    subgraph BODHI["BODHI GATE (optional) — separate platform, a real 13-node Bodhi workflow — gates the shortlist before it goes out"]
      direction TB
      C11["Risk Analyst Agent — live web research on each pick's real-world risk"]:::bodhiGate
      C12["Contrarian Agent — deliberately challenges the Risk Analyst — a real second opinion"]:::bodhiGate
      C13{"Escalation needed?"}:::bodhiGate
      C14["Escalation Analyst — a DEEPER review, only when the Contrarian flags real concern"]:::bodhiGate
      C11 -->|challenges| C12
      C12 --> C13
      C13 -.->|yes, sometimes| C14
      C13 -->|no, usually| BODHIOUT["combined verdict"]:::bodhiGate
      C14 --> BODHIOUT
    end

    subgraph HUMAN["HUMAN APPROVAL — nothing is sent without a person reviewing it first"]
      direction TB
      H1["Pending run saved — NOT sent yet"]:::humanGate
      H2["A human approves — CLI, dashboard, OR the standalone approval web service"]:::humanGate
      H1 --> H2
    end

    MICROSVC["Approval Microservice (new) — the same approve/publish logic,<br/>also runnable as its own standalone, independently deployable web service"]:::humanGate

    TG["Telegram — every pick alerted, verdict shown inline"]:::output
    TV["TradingView (local) — verdict-gated watchlist push"]:::output
    XL["Excel Tracker — portfolio_tracker.xlsx, logs every pick and verdict"]:::output

    YF --> A1
    NSE -.->|index list| A1
    GN -.->|headlines| A5
    YF -.->|5-min bars| B8

    MODEL -.->|AI call| A5
    MODEL -.->|AI call| A6
    MODEL -.->|AI call| A7
    MODEL -.->|AI call| A8
    MODEL -.->|AI call, only if a retry is needed| A9
    MODEL -.->|AI call| B9
    MODEL -.->|optional logging| LS

    T1 & T2 -.->|verification signal| A8
    T1 & T2 -.->|verification signal| A10

    A10 --> SHORTLIST
    B9 --> LIVEALERT

    SHORTLIST -->|always| H1
    SHORTLIST -.->|if Bodhi is on| C11
    BODHIOUT -.->|verdict logged| H1
    H2 --> TG & TV & XL
    H2 -.-> MICROSVC
    LIVEALERT -->|instant alert| TG
```

### Legend

| Style | Meaning |
|---|---|
| ⬜ Grey | Code only — plain math, no AI, no cost |
| 🟦 Blue | AI call — happens every time this box runs |
| 🟧 Orange | Router/controller — an AI call, but only 0 or 1 times, only when its own check finds something worth fixing |
| 🟥 Red | Deterministic safety override — no AI involved, can't be talked out of it |
| 🟪 Purple, dashed | Optional — a separate platform (Bodhi), gates the shortlist before it goes out |
| 🟩 Teal | Human-in-the-loop — a real person has to act before anything moves forward |
| 🟩 Green | Output / deliverable |
| 🟨 Yellow | The one governed model-access point every AI call goes through |

Lines: solid = always happens. Dashed = an AI call, an optional/conditional path, or an audit/logging path.

---

## What changed since the last architecture review

| | Before | Now |
|---|---|---|
| AI calls per daily scan | ~2 (News Reader + Decision Manager) | **4** — News Reader + Bull Case Agent + Bear Case Agent (run once, genuinely in parallel) + Decision Manager |
| Who argues the case | Decision Manager alone, both sides | Bull Case Agent and Bear Case Agent argue independently — **neither sees the other's output** — Decision Manager now *adjudicates* between them instead of inventing both sides itself |
| Self-checking | None | **Orchestrator** — a real controller that checks the Decision Manager's own output for real issues and can send it back for exactly one bounded retry, with specific feedback on what to fix |
| Wrong-but-confident picks | Not specifically guarded against | **Reliability Guardrail** — if two independent tools both fail to confirm a picked stock is real, it's automatically forced to "avoid," no matter how confidently the AI argued for it |
| Model | Google Gemini, fixed | **Bodhi LLM Gateway only** — currently routed to `gpt-5`, confirmed against the account's real, live model catalog (30+ real models, spanning OpenAI/Anthropic/Gemini/Amazon families, all reached through the one gateway) |
| Model choice per agent | One fixed model for every call | **Deliberate per-agent routing available** — a simple, classification-shaped decision (like the Orchestrator's retry-or-finish call) can be routed to a cheaper/faster model than the Decision Manager's real synthesis work, opt-in, tested against the real request body |
| AI spending | No ceiling | **A real, enforced cap per request** — refuses an oversized request BEFORE it's sent, not just flagged after. Sized against this system's own actual largest real prompt, not a guess |
| Bodhi workflow shape | 2 flat agents in a straight line (Risk Analyst → Contrarian) | **A real 13-node graph**: adds a nested, reusable sub-workflow, a conditional escalation branch (a second, DEEPER review agent that only runs when the Contrarian actually flags real concern — not on every run), and a structured "preview" of the final payload. See the detailed diagram below |
| Tool use | tradingview-mcp for one thing (watchlist push) | tradingview-mcp now used for **three** things (watchlist push + a live quote + a symbol check, the last two pulled in *while the Decision Manager is reasoning*), plus a **second, independent MCP server this project built and owns**, running locally, for a second real verification check |
| Before anything is sent | Not shown | **Human approval, explicit and unavoidable** — every finished shortlist is saved as *pending*; nothing reaches Telegram or TradingView until a person reviews and approves that specific run |
| Deployment | One local process | The approval/publish step can now **also** run as its own small, independently deployable web service (its own container image), separate from the daily scan |

---

## Bodhi workflow — the real graph, in detail

The high-level diagram above shows the Bodhi gate as a single grouped box
to stay readable. This is what's actually running on Bodhi's platform —
the exact structure, confirmed against a real, live, completed run
(id `9248a51c-aea8-4376-8bd8-345954cf842a`), not a design sketch:

```mermaid
flowchart TB
    classDef bodhiNode fill:#f3e6ff,stroke:#8855cc,color:#4b2a80
    classDef bodhiTool fill:#e0d0f5,stroke:#6633aa,color:#3d1a66,stroke-dasharray: 3 2
    classDef bodhiControl fill:#ffffff,stroke:#8855cc,color:#4b2a80,stroke-width:2px
    classDef bodhiOut fill:#d9f2d9,stroke:#339933,color:#1a4d1a

    Start(["start — receives today's real shortlist"]):::bodhiControl
    Sub["Sub Workflow — a SEPARATE, reusable Bodhi workflow<br/>(its own quick symbol/risk check; runs independently,<br/>not yet wired back into the verdict below)"]:::bodhiNode
    MCP1["bodhi-deep-research (tool)"]:::bodhiTool
    Risk["Risk Analyst Agent — live web research per stock"]:::bodhiNode
    Contra["Contrarian Agent — challenges the Risk Analyst's findings"]:::bodhiNode
    SetFlag["Set: extract escalation flag"]:::bodhiControl
    Needs{"Needs escalation?"}:::bodhiControl
    MCP2["bodhi-deep-research (tool)"]:::bodhiTool
    Escal["Escalation Analyst — a DEEPER review,<br/>only when the Contrarian flags real concern"]:::bodhiNode
    SetVerdict["Set: combine final verdict"]:::bodhiControl
    SetValidate["Set: validate final payload"]:::bodhiControl
    Preview["Preview Final Payload — structured dashboard summary"]:::bodhiOut
    End(["End — the workflow's official output"]):::bodhiControl

    Start --> Risk
    Start --> Sub
    MCP1 --> Risk
    Risk --> Contra
    Contra --> SetFlag
    SetFlag --> Needs
    Needs -->|yes, sometimes| Escal
    MCP2 --> Escal
    Needs -->|no, usually| SetVerdict
    Escal --> SetVerdict
    SetVerdict --> SetValidate
    SetValidate --> End
    SetValidate --> Preview
```

What each new piece actually buys us:
- **The escalation branch** means every pick doesn't get the same fixed
  amount of scrutiny — a stock the Contrarian is genuinely unsure about
  gets a real, additional, deeper review; a clean one doesn't pay that
  cost. In the run this was confirmed against, the branch outcome was
  "clean — no escalation needed," which is itself evidence the condition
  is real and doesn't fire on every run.
- **The sub-workflow** is a separately reusable Bodhi workflow, called
  from inside this one — real workflow composition, not everything
  flattened into one graph. Honest caveat: right now it runs as an
  independent leaf (its own output isn't yet merged back into the
  combined verdict below it) — a known, deliberate simplification, not
  an oversight.
- **The preview node** gives the finished payload a structured,
  dashboard-style summary as part of the workflow's own output, not just
  raw text.

---

## Low-level architecture — one real daily run, step by step

```mermaid
sequenceDiagram
    participant You
    participant Run as run.py
    participant Data as Yahoo/News/NSE
    participant Scout as Scout + 3 specialists
    participant News as News Reader (AI)
    participant BullBear as Bull/Bear Case Agents (AI)
    participant Memory as Memory (goal tracking)
    participant Mgr as Decision Manager (AI)
    participant Orch as Orchestrator (AI, conditional)
    participant Guard as Reliability Guardrail
    participant Bodhi as Bodhi Gate (optional)
    participant Human as Human approval
    participant Out as Telegram / TradingView / Excel

    You->>Run: uv run python run.py
    Run->>Data: fetch real prices, financials, headlines, index list
    Data-->>Run: real data bundles, one per stock
    Run->>Scout: score everything, no AI
    Scout-->>Run: ~10-stock watchlist + 4 scores each
    Run->>News: read real headlines — 1 AI call
    News-->>Run: sentiment + event type per stock
    Run->>Memory: what changed since last time + any open goal from a past call
    par Bull case
        Run->>BullBear: argue the strongest case FOR — AI call
    and Bear case
        Run->>BullBear: argue the strongest case AGAINST — AI call
    end
    BullBear-->>Run: two independent, unlinked analyses
    Run->>Mgr: adjudicate — all real data + both cases — AI call
    Mgr-->>Run: shortlist: opportunity, confidence, bull/bear, target, invalidation
    Run->>Orch: quality-check the Manager's own output
    alt a real, fixable issue was found
        Orch->>Mgr: retry once with specific feedback — AI call
        Mgr-->>Run: revised shortlist
    else clean
        Orch-->>Run: accepted as-is, 0 extra AI calls
    end
    Run->>Guard: check each pick against both verification tools
    alt both tools failed to confirm the symbol
        Guard->>Run: force this pick to "avoid" — no AI involved
    else at least one confirmed it
        Guard-->>Run: pick stands as the Manager gave it
    end
    opt BODHI_ENABLED=true
        Run->>Bodhi: send the real shortlist for an independent second opinion
        Bodhi-->>Run: clear/caution/avoid per stock, full narrative report
    end
    Run->>Human: save as PENDING — nothing sent yet
    Human->>Human: review (CLI, dashboard, or the approval web service)
    Human->>Out: approve → Telegram + TradingView + Excel, all together
```

### Component map

| Layer | File(s) | AI? |
|---|---|---|
| Conductor | `run.py` / `intraday_run.py` | No |
| Zero-AI scoring | `agents/research_agent.py`, `fundamental_agent.py`, `technical_agent.py`, `risk_agent.py`, `intraday_agent.py` | No |
| News reading | `agents/sentiment_agent.py` | Yes — 1 batched call |
| Independent debate | `agents/bull_case_agent.py`, `agents/bear_case_agent.py`, `agents/research_graph.py` (real parallel fan-out) | Yes — 2 batched calls, parallel |
| Final adjudication | `agents/decision_agent.py` | Yes — 1 call per risk profile selected |
| Self-checking controller | `agents/orchestrator_agent.py` | Only if a retry is needed |
| Memory + goal tracking | `memory/memory_layer.py`, `memory/knowledge_base.py` | No |
| Real-time tool calls | `integrations/tradingview_quote.py` (TradingView, via its local CLI), `integrations/mcp_server.py` + `integrations/mcp_pipeline_client.py` (this project's own MCP server, over stdio) | No — plain lookups |
| Deterministic safety net | `safety/verification_guardrail.py` | No |
| Untrusted-input guardrail | `safety/text_safety.py` | No |
| Governed model access | `config.py` — the one function (`call_llm()`) every AI call above goes through | — |
| Independent risk review | Bodhi Workflow Builder graph — 13 real nodes (Risk Analyst, Contrarian, a conditional Escalation Analyst, a nested sub-workflow, a preview node), called from `integrations/bodhi_risk_insights.py` | Yes, on Bodhi's own platform |
| Human approval | `alerts/approval_gate.py`, `approve_run.py`, `dashboard.py`, `service/approval_service.py` | No |
| Delivery | `alerts/telegram_alert.py`, `alerts/tradingview_watchlist.py`, `alerts/excel_report.py` | No |

---

## Model & governance details

- **Single entry point.** Every AI call in this codebase — no exceptions — goes through `config.py::call_llm()`, which talks only to the Bodhi LLM Gateway. No provider SDK (OpenAI, Anthropic, Google) is imported anywhere.
- **Model in use today:** `gpt-5`, confirmed against the account's real, live model catalog.
- **Deliberate per-agent routing (optional):** a simple, classification-shaped decision (like the Orchestrator's retry-or-finish call) can be routed to a different, cheaper model than the Decision Manager's real synthesis work — a stated choice, not a blanket setting.
- **Enforced spending cap:** a real ceiling on how large a single request is allowed to be, checked *before* anything is sent — refuses an oversized call outright rather than just warning after the fact. The current limit is set with real headroom above the largest request this system actually produces, not picked arbitrarily.

## MCP tools actually used

- **tradingview-mcp** (external) — this project's one direct connection to TradingView Desktop, used for three real things now: pushing approved picks to a real watchlist, pulling a live quote while the Decision Manager reasons, and confirming a ticker is real before any AI time is spent on it.
- **This project's own local MCP server** (new) — built and owned end to end, running locally over the standard stdio transport, offering a second, independent way to confirm a stock is real plus structured sector-risk notes. Needs nothing else open or running.
- **bodhi-deep-research** — used internally by Bodhi's own Risk Analyst Agent, on Bodhi's platform. Not called directly by this codebase.
- Everything else (Yahoo Finance, Google News, NSE, Telegram, Excel, LangSmith) is a plain API or library call, not MCP.
