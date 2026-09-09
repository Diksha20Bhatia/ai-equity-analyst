# Sample telemetry — what a real run's evidence looks like

These four `.sample.jsonl` files are **generated, not fabricated by
hand** — `scripts/generate_sample_telemetry.py` calls this project's
actual `observability.record_call()` / `record_failure()` /
`record_orchestrator_decision()` / `record_tool_call()` functions, the
same ones every real `run.py` execution calls, against a small
deterministic scenario matching `tests/test_scenario_full_pipeline.py`
and `tests/test_scenario_bodhi_branches.py`. The JSON shape, field
names, and formatting are guaranteed to match a real run's output
because it's the same code writing them.

**What these are NOT**: a captured real production run, or a real
incident. This project's own rule (see the top-level README) is to never
narrate a specific real failure in checked-in documentation — the one
`reliability_log` line here is a generic, illustrative retry shape (a
transient rate limit), not an account of anything that actually happened.

**Your own real ones** land in `output/` (`llm_call_log.jsonl`,
`reliability_log.jsonl`, `orchestrator_decisions.jsonl`,
`tool_call_log.jsonl`) after any real `run.py` execution — gitignored,
never checked in, and readable from the dashboard's 📜 Activity tab
without opening a file at all.

Regenerate these samples any time:
```bash
uv run python scripts/generate_sample_telemetry.py
```

| File | What a real one tells you |
|---|---|
| `llm_call_log.sample.jsonl` | every successful AI call, real latency + token usage, per agent |
| `reliability_log.sample.jsonl` | every Bodhi Gateway retry/timeout/failure — typed (`BodhiGatewayAuthError`/`ShapeError`/`UnavailableError`, see `config.py`) |
| `orchestrator_decisions.sample.jsonl` | every real retry-vs-finish branch decision from the decide→reflect→route loop, with its reason |
| `tool_call_log.sample.jsonl` | every real tool call an agent makes mid-reasoning — a live TradingView quote, a symbol-search verification |
