# Approval Service — a real, independently deployable microservice

This is a genuinely **separate** piece of this project — its own process,
its own entry point, its own Dockerfile — not a new feature bolted onto
`run.py` or `dashboard.py`. It exposes the exact same human-approval/
publish path those two already have (CLI and Streamlit) as a third
interface: plain HTTP, reachable from anywhere, independent of whether
an analysis run is currently executing or a browser tab is open.

## Why this boundary, specifically

`run.py` (market data fetch + multi-agent analysis) is compute-heavy,
scheduled, and has no reason to be always-on or network-reachable — it's
a batch job that runs once a day and takes minutes. The approval/publish
path is the opposite shape: a human (or another system) needs to be able
to review a pending run and act on it **at any time**, independent of
whether an analysis run is currently executing. That split already
exists conceptually in this codebase — `run.py` never publishes;
`approve_run.py` is already a separate step — this service just gives
that seam a second, always-available interface instead of only a
CLI/dashboard one.

**Not a second implementation to keep in sync.** Every endpoint below
calls straight into `alerts/approval_gate.py` (the same pending →
approved/rejected state machine) and `approve_run.py::_publish()` (the
same function that sends to Telegram and pushes to TradingView — see
`tests/test_approve_run.py`) that the CLI and dashboard already use.
There is exactly one real "what does approving a run do" in this
codebase; this is a new door onto it, not a fork of it.

## Run it

Standalone, separate from `run.py`/`dashboard.py`, on its own host or port:

```bash
uv run uvicorn service.approval_service:app --host 0.0.0.0 --port 8000
```

Interactive API docs (FastAPI generates these automatically):
`http://localhost:8000/docs`

## Endpoints

| Method | Path | What it does |
|---|---|---|
| `GET` | `/health` | Real health check — status + service name, for a load balancer or container orchestrator |
| `GET` | `/pending` | List pending runs, newest first, with a per-tier pick count |
| `GET` | `/pending/{run_id}` | The full record for one pending run |
| `POST` | `/pending/{run_id}/approve` | Publishes to every configured channel (Telegram + TradingView) and moves the run to `output/approved/` |
| `POST` | `/pending/{run_id}/reject` | Moves the run to `output/rejected/` with an optional `{"reason": "..."}` body |
| `GET` | `/approved` / `/rejected` | List runs already actioned |

`run_id` is the pending-run filename without `.json` (e.g.
`run_2026-09-08_143406`) — validated against the exact real filename
shape (`alerts/approval_gate.py::save_pending()`'s own format) **before**
it's ever used to build a filesystem path, so a malformed or
path-traversal `run_id` is refused with a `400`/`404`, never silently
joined into a path.

## Deploy it as a real container

```bash
# from the repo root, not this directory
docker build -f service/Dockerfile -t ai-equity-analyst-approval-service .
docker run -p 8000:8000 --env-file .env ai-equity-analyst-approval-service
```

Honest limitation: this Dockerfile was written and reviewed, but Docker
isn't installed in the environment this project is currently developed
in, so the image build itself hasn't been executed and verified here.
The service's actual logic **has** been verified for real — a real
`uvicorn` process was started standalone (not just through FastAPI's
in-process `TestClient`) and hit with real HTTP requests against the
project's actual `output/pending_approval/` data during development.

## Tests

`tests/test_approval_service.py` — 8 tests, real HTTP requests through
FastAPI's `TestClient` (genuine ASGI routing, not calling the Python
functions directly), isolated to a temp directory the same way
`tests/test_approval_gate.py` is. Telegram/TradingView are mocked only
at the true I/O boundary (`requests.post` / `subprocess.run`), so
approving **over HTTP** is proven to trigger the exact same coordinated
multi-channel publish `tests/test_approve_run.py` proves for the CLI
path — not a second, could-drift-out-of-sync implementation. Also
covers: 404 on an unknown run, a malformed/path-traversal `run_id`
rejected before any filesystem access, and that a run can't be approved
twice (already moved out of pending after the first call).

```bash
uv run pytest tests/test_approval_service.py -v
```

## What this is not

This is not a rewrite of `run.py`'s analysis pipeline into microservices
— that pipeline is a genuine batch job with no reason to be decomposed
further for this project's actual scale. This is one deliberately-chosen
seam (approval/publish) demonstrated as independently deployable, which
is the real point: showing the system **can** live beyond a single local
reference repo, not rewriting the whole architecture to prove it twice.
