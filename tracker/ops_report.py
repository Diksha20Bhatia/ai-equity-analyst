"""
ops_report.py  —  what actually ran, what it cost, and what broke
=====================================================================
    uv run python tracker/ops_report.py

Reads the always-on telemetry this app writes on every run —
output/metrics.jsonl and output/reliability_log.jsonl (observability.py) —
and prints a real operational summary: runs, per-agent latency and token
cost, LLM error rate, retrieval activity, prompt-safety events, and recent
failures.

No configuration, no API key, no external tracing service. If the app has
run at all, this has data. That is the point: runtime visibility should
not depend on someone having set up a hosted tracing product.

The dashboard's "🛠 Ops & evidence" tab renders the same records; this is
the terminal view of it.
"""

import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import observability  # noqa: E402


def _pct(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "—"


def build_report() -> dict:
    metrics = observability.read_metrics()
    failures = observability.read_failures()

    runs = defaultdict(list)
    for m in metrics:
        runs[m.get("run_id", "adhoc")].append(m)

    llm_calls = [m for m in metrics if m.get("event") == "llm_call"]
    llm_errors = [m for m in llm_calls if m.get("status") == "error"]
    retrievals = [m for m in metrics if m.get("event") == "kb_retrieval"]
    safety = [m for m in metrics if m.get("event") == "safety_event"]
    reviews = [m for m in metrics if m.get("event") == "bodhi_review"]
    approvals = [m for m in metrics if m.get("event") == "approval_decision"]

    by_agent = defaultdict(lambda: {"calls": 0, "errors": 0, "ms": 0, "tokens": 0})
    for m in llm_calls:
        entry = by_agent[m.get("agent", "unknown")]
        entry["calls"] += 1
        entry["ms"] += m.get("duration_ms") or 0
        entry["tokens"] += m.get("total_tokens") or 0
        if m.get("status") == "error":
            entry["errors"] += 1

    latencies = sorted(m["duration_ms"] for m in llm_calls if m.get("duration_ms"))

    return {
        "runs": len(runs),
        "llm_calls": len(llm_calls),
        "llm_errors": len(llm_errors),
        "llm_error_rate": _pct(len(llm_errors), len(llm_calls)),
        "total_tokens": sum(m.get("total_tokens") or 0 for m in llm_calls),
        "median_latency_ms": latencies[len(latencies) // 2] if latencies else None,
        "p95_latency_ms": latencies[int(len(latencies) * 0.95)] if latencies else None,
        "retrievals": len(retrievals),
        "retrieval_empty": sum(1 for r in retrievals if not r.get("returned")),
        "safety_events": len(safety),
        "safety_spans": sum(s.get("finding_count") or 0 for s in safety),
        "bodhi_reviews": len(reviews),
        "approvals": len([a for a in approvals if a.get("decision") == "approved"]),
        "rejections": len([a for a in approvals if a.get("decision") == "rejected"]),
        "failures": len(failures),
        "by_agent": dict(by_agent),
        "recent_failures": failures[-10:],
        "recent_safety": safety[-10:],
    }


def print_report(r: dict) -> None:
    if not r["runs"] and not r["llm_calls"]:
        print("No telemetry recorded yet — run the pipeline once "
              "(`uv run python run.py`) and this fills in. No setup needed.")
        return

    print("OPERATIONS REPORT  (from always-on local telemetry)")
    print("=" * 58)
    print(f"  Runs recorded          : {r['runs']}")
    print(f"  LLM calls              : {r['llm_calls']}  "
          f"({r['llm_errors']} errors, {r['llm_error_rate']} error rate)")
    print(f"  Tokens consumed        : {r['total_tokens']:,}" if r["total_tokens"]
          else "  Tokens consumed        : not reported by the gateway")
    if r["median_latency_ms"]:
        print(f"  LLM latency            : median {r['median_latency_ms'] / 1000:.1f}s, "
              f"p95 {r['p95_latency_ms'] / 1000:.1f}s")
    print(f"  Knowledge retrievals   : {r['retrievals']} "
          f"({r['retrieval_empty']} returned nothing)")
    print(f"  Prompt-safety events   : {r['safety_events']} "
          f"({r['safety_spans']} span(s) neutralised)")
    print(f"  Bodhi reviews          : {r['bodhi_reviews']}")
    print(f"  Human decisions        : {r['approvals']} approved, {r['rejections']} rejected")
    print(f"  Failures logged        : {r['failures']}")

    if r["by_agent"]:
        print()
        print("  Per agent:")
        for agent, v in sorted(r["by_agent"].items()):
            avg = v["ms"] / v["calls"] / 1000 if v["calls"] else 0
            print(f"    {agent:26s} {v['calls']:3d} call(s), {v['errors']} error(s), "
                  f"avg {avg:5.1f}s, {v['tokens']:,} tokens")

    if r["recent_safety"]:
        print()
        print("  Recent prompt-safety events:")
        for s in r["recent_safety"]:
            findings = ", ".join(str(f) for f in (s.get("findings") or [])[:3])
            print(f"    {s.get('timestamp', '')} [{s.get('source', '?')}] "
                  f"{s.get('finding_count', 0)} span(s): {findings}")

    if r["recent_failures"]:
        print()
        print("  Recent failures:")
        for f in r["recent_failures"]:
            print(f"    {f.get('timestamp', '')} [{f.get('component', '?')}] "
                  f"{str(f.get('detail', ''))[:90]}")


if __name__ == "__main__":
    print_report(build_report())
