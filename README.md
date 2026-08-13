# 🧠 AI Equity Analyst

An **agentic AI** that behaves like a small equity research desk for the
**Indian stock market** — powered by Google **Gemini**.

Instead of you opening a stock screener and manually checking 500 companies
every morning, a team of AI "specialists" does it for you and hands you a
short, reasoned shortlist of 2–3 high-conviction ideas.

> **Not a chatbot. Not a buy/sell signal generator.**
> A workflow that *observes → reasons → prioritises → acts*.

---

## 📖 Read this first (the idea in plain English)

Think of it like **hiring a small team**, where each person has exactly one job,
and a manager who makes the final call.

| Who | Nickname | What they do (in plain words) |
|-----|----------|-------------------------------|
| **Research Agent** | The Scout | Every morning, scans the whole basket of stocks and points at the few doing something *unusual* — big price moves, volume spikes, block deals. |
| **Fundamental Agent** | The Analyst | Checks if the actual *business* is healthy — growing revenue, good profit margins, not drowning in debt. |
| **Technical Agent** | The Chart Reader | Looks only at the *price chart* — is it trending up, breaking out, backed by real volume? |
| **Sentiment Agent** | The News Reader | Reads the *news and filings* around the stock — is the mood positive or negative? |
| **Risk Agent** | The Skeptic | Actively hunts for *red flags* — promoters selling shares, shady earnings, thin trading. |
| **Memory Layer** | The Notebook | Remembers past analysis so the system *learns context over time* (e.g. "this stock keeps showing up"). |
| **Decision Agent** | The Manager | Reads *everyone's* report, respects *your* preferences, and writes the final shortlist with reasoning. |

The big shift: instead of **you** reading everything, the system reads it,
argues it out internally, and gives you a short answer you can act on.

---

## 🗺️ How one morning run flows

```
                 ┌─────────────────────────────────────────┐
                 │   Market & News Data (NSE · BSE · News)  │
                 └─────────────────────────────────────────┘
                                     │
                                     ▼
        ┌──────────────────────────────────────────────────────┐
        │  1. RESEARCH AGENT  →  picks a short watchlist        │
        └──────────────────────────────────────────────────────┘
                                     │
          ┌──────────────┬───────────┼───────────┬──────────────┐
          ▼              ▼           ▼           ▼              ▼
    ┌──────────┐  ┌───────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
    │Fundamental│ │ Technical │ │Sentiment │ │   Risk   │ │  Memory  │
    │  Agent   │  │  Agent    │ │  Agent   │ │  Agent   │ │  recall  │
    └──────────┘  └───────────┘ └──────────┘ └──────────┘ └──────────┘
          └──────────────┴───────────┬───────────┴──────────────┘
                                     ▼
        ┌──────────────────────────────────────────────────────┐
        │  2. DECISION AGENT  (also reads YOUR investor profile)│
        │     → final shortlist of 2–3 ideas + reasoning        │
        └──────────────────────────────────────────────────────┘
                                     │
                                     ▼
              📱 Telegram alert   +   💾 saved to /output
```

---

## 🧩 What each file is (so nothing feels like a black box)

```
ai-equity-analyst/
├── run.py                  ← the ONE file you run. The "conductor".
├── config.py               ← reads .env, logs in to Gemini
├── .env.example            ← copy this to .env and add your settings
├── requirements.txt        ← the Python libraries to install
├── sample_profile.txt      ← an example investor profile you can edit
│
├── agents/                 ← the "team members"
│   ├── base_agent.py       ← shared skill: "ask Gemini, get JSON back"
│   ├── research_agent.py   ← The Scout
│   ├── fundamental_agent.py← The Analyst
│   ├── technical_agent.py  ← The Chart Reader
│   ├── sentiment_agent.py  ← The News Reader
│   ├── risk_agent.py       ← The Skeptic
│   └── decision_agent.py   ← The Manager
│
├── data/market_data.py     ← where the numbers come from (demo or live)
├── memory/memory_layer.py  ← The Notebook (remembers past runs)
├── profile/profile_loader.py← reads your profile (PDF/HTML/DOCX/txt)
├── alerts/telegram_alert.py← sends the shortlist to Telegram
└── output/                 ← each run's full analysis is saved here as JSON
```

---

## 🚀 Getting started — step by step

You have **two ways to run**. Start with the easy one.

### ✅ Option A — "Just show me it works" (demo mode, no keys, no internet)

This runs the entire pipeline using built-in sample data and simple
rule-based logic. It's the fastest way to *see the whole thing work* before
you set up Gemini.

**Step 1 — Install Python 3.10+**
Check with:
```bash
python3 --version
```

**Step 2 — Install the libraries**
```bash
cd ai-equity-analyst
pip install -r requirements.txt
```
> If any single library fails, the app still runs — it degrades gracefully.
> At minimum you need `python-dotenv`.

**Step 3 — Create your settings file**
```bash
cp .env.example .env
```
Open `.env` and make sure this line says `demo`:
```
DATA_MODE=demo
```
That's it — you don't need any API key for demo mode.

**Step 4 — Run it**
```bash
python run.py
```

You'll watch each agent work and get a final shortlist printed at the bottom.
A full copy is saved in `output/analysis_YYYY-MM-DD.json`.

---

### 🔵 Option B — "Real AI reasoning" (with Gemini)

Now let's plug in **Gemini** so the agents actually *reason* instead of using
simple rules. Pick **one** of the two login methods below.

#### Method 1 — Google AI Studio API key (easiest)

1. Go to **https://aistudio.google.com/apikey** and create a free API key.
2. In your `.env`, set:
   ```
   GOOGLE_AUTH_MODE=token
   GOOGLE_GENAI_USE_VERTEXAI=FALSE
   GOOGLE_API_KEY=paste-your-key-here
   GOOGLE_MODEL=gemini-3.5-flash
   ```
3. Run:
   ```bash
   python run.py
   ```

#### Method 2 — Google Cloud / Vertex AI (what your .env is set up for)

Use this if you have a Google Cloud project (yours is
`burner-dikbhati1-01`).

1. **Install the gcloud CLI**: https://cloud.google.com/sdk/docs/install
2. **Log in** so your machine gets credentials (this is "ADC"):
   ```bash
   gcloud auth application-default login
   ```
3. **Enable the Vertex AI API** on your project (one time):
   ```bash
   gcloud services enable aiplatform.googleapis.com --project burner-dikbhati1-01
   ```
4. In your `.env`, keep:
   ```
   GOOGLE_AUTH_MODE=adc
   GOOGLE_GENAI_USE_VERTEXAI=TRUE
   GOOGLE_CLOUD_PROJECT=burner-dikbhati1-01
   GOOGLE_CLOUD_LOCATION=europe-west2
   GOOGLE_MODEL=gemini-3.5-flash
   ```
5. Run:
   ```bash
   python run.py
   ```

> **`GOOGLE_AUTH_MODE=auto`** tries Vertex AI first and falls back to the API
> key automatically — handy if you're not sure which will work.

When Gemini is connected, the top of the output will say
`Gemini login: adc (connected)` or `token (connected)`.
If it says `FALLBACK MODE — no LLM`, your credentials didn't load — re-check the
step above (the app still runs with rule-based logic in the meantime).

---

### 🟢 Option C — "Real market data" (live prices)

To use **real NSE prices** instead of sample data:

1. Make sure `yfinance` installed correctly (`pip install yfinance`).
2. In `.env` set:
   ```
   DATA_MODE=live
   ```
3. Run `python run.py`.

Live mode pulls real price/volume from Yahoo Finance (using the `.NS` suffix
for NSE). **Note:** deep fundamentals, live news, and promoter/pledge data
need paid APIs — those fields currently fall back to sample values, and the
code marks exactly where to plug a real provider in (`data/market_data.py`).

---

## 🙋 Adding your own investor profile

The Decision Agent can personalise picks to **you**.

1. Write your preferences in a file — **any** of these formats works:
   `.txt`, `.md`, `.pdf`, `.html`, `.docx`.
   (See `sample_profile.txt` for an example you can copy.)
2. Point `.env` at it:
   ```
   PROFILE_PATH=my_profile.pdf
   ```
3. Run as usual. The system reads your file, turns it into a clean structured
   profile, and the Decision Agent will **skip anything that breaks your
   exclusions** (e.g. "no tobacco", "no penny stocks") and lean toward your
   preferred sectors and risk level.

---

## 📱 Getting alerts on Telegram (optional)

1. On Telegram, message **@BotFather**, send `/newbot`, and copy the **token**.
2. Message your new bot once (say "hi").
3. Message **@userinfobot** to get your **chat id**.
4. Put both in `.env`:
   ```
   TELEGRAM_BOT_TOKEN=your-token
   TELEGRAM_CHAT_ID=your-chat-id
   ```
Now every run pings your phone. Leave these blank and it just prints to screen.

---

## ⏰ Running it automatically every morning

On Mac/Linux, use `cron`. To run at 8:00 AM every weekday:
```bash
crontab -e
```
Add this line (adjust the path):
```
0 8 * * 1-5  cd /path/to/ai-equity-analyst && /usr/bin/python3 run.py >> output/cron.log 2>&1
```

On Windows, use **Task Scheduler** to run `python run.py` on a daily trigger.

---

## 🔌 Where the "MCP" and real data sources fit

The system is built so each agent's data source can be swapped for a real one
(including MCP servers where they exist):

| Agent | Data it needs | Ready-made options |
|-------|---------------|--------------------|
| Research | price / volume / block deals | `stock-nse-india` (has an **MCP server**), `nse-bse-api`, Kite Connect |
| Fundamental | revenue, ROCE, debt | Indian Stock API (indianapi.in), Screener.in |
| Technical | OHLC history | same price feed + `pandas-ta` / `TA-Lib` |
| Sentiment | news & filings | NewsAPI, GNews, BSE announcements |
| Risk | shareholding, deals | shareholding pattern + bulk/block deal reports |
| Memory | — | ChromaDB (local, already wired) |

Only the Research Agent has a plug-and-play MCP server today; the others
connect via normal REST calls inside their agent file. Each `data/market_data.py`
function has a comment marking exactly where to drop a real API in.

---

## 🛠️ Troubleshooting

| You see | What it means | Fix |
|---------|---------------|-----|
| `google-genai not installed` | Gemini library missing | `pip install google-genai` |
| `Gemini login: none (FALLBACK MODE)` | credentials didn't load | recheck Option B; app still runs on rules |
| `no live data for X, using demo` | Yahoo had no data / rate-limited | try again, or use `DATA_MODE=demo` |
| `chromadb` errors | vector DB missing | ignore — it auto-falls back to a JSON notebook |
| PDF/DOCX profile not read | parser lib missing | `pip install pypdf python-docx beautifulsoup4` |

---

## ⚠️ Important

This project is for **learning and research**. It does **not** give
personalised financial advice, and its output should never be treated as a
recommendation to buy or sell. Markets are risky — always do your own
research and consult a SEBI-registered advisor before investing.

---

## 🧭 What to build next

- Connect a real fundamentals + news API for the Fundamental/Sentiment agents.
- Add the Research Agent's MCP server for live NSE scanning.
- Add automated portfolio allocation and risk-adjusted position sizing.
- Add a feedback loop: track how past shortlists performed and let Memory
  learn from it.
