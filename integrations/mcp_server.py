"""
mcp_server.py  —  a real MCP server for this project, all three server
primitives (Tools, Resources, Prompts), stdio transport
=====================================================================
Everywhere else in this codebase, "MCP" meant one thing: Bodhi's own
`bodhi-deep-research` node, reached over HTTP, exposing exactly one
primitive (Tools). This is a second, genuinely different MCP surface —
built and controlled by THIS project, using the official `mcp` Python
SDK, real stdio transport (a different transport class from Bodhi's
HTTP, per the MCP spec: local process pipes vs. remote HTTP/SSE), and
two primitives beyond Tools:

  - **Tool** `verify_nse_symbol` — confirms a ticker is a real, current
    NIFTY50 constituent, using this project's own real, live-fetched
    universe (data/nse_universe.py) — no network call from the SERVER
    itself beyond what that module already does, and no dependency on
    TradingView Desktop being open (unlike integrations/tradingview_quote.py's
    CDP-based check — a genuinely different verification path, not a
    duplicate).
  - **Resource** `sector-notes://{sector}` — serves this project's own
    real sector risk source documents (the same ones
    memory/knowledge_base.py chunks and indexes for retrieval), exposed
    here as raw, addressable MCP Resources instead of only being
    reachable through the RAG pipeline.
  - **Prompt** `risk_review_prompt` — a reusable, parameterized prompt
    template an MCP client can fetch and reuse, instead of every
    consumer hand-copying the same prompt text.

Run standalone to test manually:
    uv run python integrations/mcp_server.py
(it will block, waiting for stdio input — that's correct; it's meant to
be spawned as a subprocess by an MCP client, see
integrations/mcp_pipeline_client.py, not run interactively)
"""

import contextlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from data.nse_universe import resolve_universe  # noqa: E402
from memory.knowledge_base import _SOURCE_DOCS  # noqa: E402


@contextlib.contextmanager
def _stdout_reserved_for_mcp_protocol():
    """A real bug this file was built against, not a hypothetical: under
    stdio transport, stdout IS the MCP JSON-RPC channel — any stray
    print() from an imported module (data/nse_universe.py logs real
    fetch status to stdout for normal CLI/run.py use) corrupts the
    protocol stream, and the client fails with a JSON parse error on
    that line. tradingview-mcp's own server.js works around the exact
    same constraint by writing its startup notice to stderr instead.
    This redirects stdout to stderr for the duration of a call into
    code that isn't MCP-aware, without changing that code's normal
    behavior for every OTHER caller (run.py, the CLI) where printing to
    stdout is correct and expected."""
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        yield
    finally:
        sys.stdout = real_stdout

mcp_app = MCPServer(
    name="ai-equity-analyst-tools",
    version="1.0.0",
    instructions=(
        "Real local tools/resources/prompts for Indian equity risk review — "
        "a second, stdio-transport MCP surface distinct from Bodhi's own "
        "HTTP-based bodhi-deep-research tool."
    ),
)


@mcp_app.tool()
def verify_nse_symbol(symbol: str) -> dict:
    """Confirm `symbol` is a real, current NIFTY50 constituent using this
    project's own live-fetched universe (data/nse_universe.py) — no
    TradingView Desktop dependency, a genuinely different verification
    path from integrations/tradingview_quote.py::verify_symbol()."""
    with _stdout_reserved_for_mcp_protocol():
        universe = resolve_universe("NIFTY50")
    lookup = {s.upper() for s in universe}
    verified = symbol.strip().upper() in lookup
    return {"symbol": symbol.strip().upper(), "verified": verified, "universe_size": len(universe)}


@mcp_app.resource("sector-notes://{sector}")
def sector_notes(sector: str) -> str:
    """Real Resource primitive: this project's own real sector risk
    source documents (memory/knowledge_base.py's _SOURCE_DOCS), exposed
    as directly addressable MCP Resources — the same real content the
    RAG pipeline chunks and indexes, reachable here without going
    through retrieval at all."""
    for doc in _SOURCE_DOCS:
        if doc["sector"].lower() == sector.strip().lower():
            return doc["text"]
    available = sorted({d["sector"] for d in _SOURCE_DOCS})
    return f"No sector notes for {sector!r}. Available sectors: {', '.join(available)}."


@mcp_app.prompt()
def risk_review_prompt(symbol: str, sector: str) -> str:
    """Real Prompt primitive: a reusable, parameterized risk-review
    template — fetched via MCP's prompts/get, not copy-pasted per
    caller. See integrations/mcp_pipeline_client.py for a real client
    that fetches and uses this."""
    return (
        f"You are a risk analyst reviewing {symbol} ({sector} sector). "
        f"Using only real, current information, identify 2-3 concrete risk "
        f"factors specific to this stock's actual sector and business model — "
        f"cite specifics, never a generic market overview."
    )


if __name__ == "__main__":
    mcp_app.run(transport="stdio")
