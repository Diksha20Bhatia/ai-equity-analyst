"""
mcp_pipeline_client.py  —  a real MCP CLIENT speaking real MCP protocol
over stdio to integrations/mcp_server.py
=====================================================================
This is the consuming half of mcp_server.py — a genuine MCP client
(the official `mcp` Python SDK's `ClientSession`), not a subprocess
CLI wrapper like integrations/tradingview_quote.py's approach. It
spawns mcp_server.py as a child process and speaks the real JSON-RPC
MCP protocol over its stdin/stdout (the stdio transport) — the second,
different transport this project now demonstrates, alongside Bodhi's
own HTTP-based bodhi-deep-research tool.

Three real calls, one per primitive:
    verify_symbol_via_mcp("RELIANCE")   -> tools/call
    get_sector_notes_via_mcp("IT")      -> resources/read
    get_risk_review_prompt_via_mcp(...) -> prompts/get

Every real call (success or failure) is logged to
output/tool_call_log.jsonl via observability.record_tool_call(), same
as every other real tool call in this project — tool="mcp_stdio_*" so
it's distinguishable in telemetry from the HTTP-transport Bodhi calls
and the CDP/REST TradingView calls.

Best-effort throughout: if the server subprocess fails to start, hangs,
or returns something unexpected, every function here returns None
rather than raising — a missing MCP result should never block or
corrupt a run, the same non-blocking discipline as every other optional
tool in this project.
"""

import asyncio
import sys
import time
from pathlib import Path

import observability
from config import settings
from schemas import ValidationError

ROOT = Path(__file__).resolve().parent.parent
_SERVER_SCRIPT = str(ROOT / "integrations" / "mcp_server.py")


async def _call_tool_async(symbol: str) -> dict | None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=sys.executable, args=[_SERVER_SCRIPT])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool("verify_nse_symbol", {"symbol": symbol})
            if result.is_error:
                return None
            for block in result.content:
                if getattr(block, "type", None) == "text":
                    import json
                    return json.loads(block.text)
    return None


async def _read_resource_async(sector: str) -> str | None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=sys.executable, args=[_SERVER_SCRIPT])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.read_resource(f"sector-notes://{sector}")
            for content in result.contents:
                if hasattr(content, "text"):
                    return content.text
    return None


async def _get_prompt_async(symbol: str, sector: str) -> str | None:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    params = StdioServerParameters(command=sys.executable, args=[_SERVER_SCRIPT])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.get_prompt("risk_review_prompt", {"symbol": symbol, "sector": sector})
            for msg in result.messages:
                content = msg.content
                if hasattr(content, "text"):
                    return content.text
    return None


def _run_best_effort(coro_factory, tool_name: str, target: str, agent: str) -> object | None:
    """Shared best-effort wrapper: runs the real async MCP call, times
    it, logs it, and never raises — matches every other optional real
    tool in this project (integrations/tradingview_quote.py)."""
    started = time.monotonic()
    result = None
    try:
        result = asyncio.run(coro_factory())
    except Exception:  # noqa: BLE001 — a missing/broken MCP server must never block a run
        result = None
    observability.record_tool_call(
        tool=tool_name, target=target, success=result is not None,
        latency_ms=(time.monotonic() - started) * 1000, agent=agent,
    )
    return result


def verify_symbol_via_mcp(symbol: str, agent: str = "Research Agent (Scout)") -> dict | None:
    """Real Tool call (tools/call) over real stdio MCP transport.
    Returns {"symbol", "verified", "universe_size"} — validated against
    schemas.McpSymbolVerification — or None if disabled
    (MCP_VERIFICATION_ENABLED) or the call failed for any reason. Never
    raises.

    The INPUT is validated (and repaired) too, not just the output —
    schemas.McpVerifySymbolInput.repair() cleans a decorated symbol
    ("nse:reliance", " Reliance \\n") into the bare ticker this tool
    actually expects before the real round-trip is made; a symbol with
    nothing usable left after cleaning is refused before any call."""
    if not settings.mcp_verification_enabled:
        return None
    from schemas import McpVerifySymbolInput
    validated_input = McpVerifySymbolInput.repair(symbol)
    if validated_input is None:
        observability.record_failure(
            "mcp_tool_input_shape", f"refused to call verify_nse_symbol — no usable symbol in {symbol!r}",
            tool="mcp_stdio_verify_symbol",
        )
        return None

    raw = _run_best_effort(
        lambda: _call_tool_async(validated_input.symbol), "mcp_stdio_verify_symbol", validated_input.symbol, agent,
    )
    if raw is None:
        return None
    from schemas import McpSymbolVerification
    try:
        return McpSymbolVerification.model_validate(raw).model_dump()
    except ValidationError:
        observability.record_failure(
            "mcp_tool_shape", f"verify_nse_symbol returned a shape that failed validation: {raw}",
            tool="mcp_stdio_verify_symbol",
        )
        return None


def get_sector_notes_via_mcp(sector: str, agent: str = "Decision Agent") -> str | None:
    """Real Resource read (resources/read) over real stdio MCP transport.
    None if disabled (MCP_VERIFICATION_ENABLED) or the call failed."""
    if not settings.mcp_verification_enabled:
        return None
    return _run_best_effort(lambda: _read_resource_async(sector), "mcp_stdio_sector_resource", sector, agent)


def get_risk_review_prompt_via_mcp(symbol: str, sector: str, agent: str = "Decision Agent") -> str | None:
    """Real Prompt fetch (prompts/get) over real stdio MCP transport.
    None if disabled (MCP_VERIFICATION_ENABLED) or the call failed."""
    if not settings.mcp_verification_enabled:
        return None
    return _run_best_effort(
        lambda: _get_prompt_async(symbol, sector), "mcp_stdio_risk_review_prompt", f"{symbol}/{sector}", agent,
    )
