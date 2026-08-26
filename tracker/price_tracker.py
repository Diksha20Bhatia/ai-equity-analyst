"""
price_tracker.py  —  "The Ledger's Auditor"
================================================
A standalone tool, deliberately separate from agents/ — it isn't part of
the daily pipeline, it's a maintenance pass you run whenever you want to
check how past picks have actually performed.

    uv run python tracker/price_tracker.py

Walks every sheet in output/portfolio_tracker.xlsx (one sheet per swing
run date, written by alerts/excel_report.py) and, for every table it
finds (Selected Stocks, Top N Watchlist), appends ONE new column dated
with today's date: each stock's real current price, plus its % change
vs. whichever column was the most recent price checkpoint for that table
— either the original "Current Price" column (first time this tool runs
against a sheet) or the last tracking column a previous run of this tool
added (every time after that).

Zero AI — this is a real price lookup and a subtraction, nothing more.
Every distinct symbol's price is fetched once per run and reused across
every sheet/table it appears in, so a stock picked on five different
days only costs one real network call, not five.
"""

import datetime as dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from data.market_data import get_market_stats  # noqa: E402

WORKBOOK_PATH = ROOT / "output" / "portfolio_tracker.xlsx"
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_PRICE_RE = re.compile(r"₹?([\d,]+\.?\d*)")


def _find_tables(ws) -> list:
    """
    Locate every table in a sheet: a header row whose column-A cell is
    literally "Symbol", followed by data rows until the first blank one.
    """
    tables = []
    row, max_row = 1, ws.max_row
    while row <= max_row:
        if ws.cell(row=row, column=1).value == "Symbol":
            data_start = row + 1
            r = data_start
            while r <= max_row and ws.cell(row=r, column=1).value not in (None, ""):
                r += 1
            data_end = r - 1
            hc = 1
            while ws.cell(row=row, column=hc).value not in (None, ""):
                hc += 1
            last_col = hc - 1
            tables.append({"header_row": row, "data_start": data_start, "data_end": data_end, "last_col": last_col})
            row = data_end + 1
        else:
            row += 1
    return tables


def _reference_price_col(ws, table: dict):
    """The rightmost column in this table's header that's a real price
    checkpoint (the original "Current Price" column, or a previous
    tracking run's date column) — skips non-price columns in between,
    like Description or Tier(s)."""
    for col in range(table["last_col"], 0, -1):
        header = str(ws.cell(row=table["header_row"], column=col).value or "")
        if "Current Price" in header or _DATE_RE.match(header):
            return col
    return None


def _parse_price(cell_value):
    if isinstance(cell_value, (int, float)):
        return float(cell_value)
    if isinstance(cell_value, str):
        m = _PRICE_RE.match(cell_value.strip())
        if m:
            return float(m.group(1).replace(",", ""))
    return None


def _real_price(symbol: str, cache: dict):
    if symbol not in cache:
        try:
            cache[symbol] = get_market_stats(symbol)["current_price"]
        except Exception as e:  # noqa: BLE001
            print(f"[tracker] {symbol}: could not fetch a real price ({e}) — skipped")
            cache[symbol] = None
    return cache[symbol]


def run():
    if not WORKBOOK_PATH.exists():
        raise RuntimeError(
            f"No workbook found at {WORKBOOK_PATH} — run run.py at least once first "
            "so there's something to track."
        )

    from openpyxl import load_workbook
    from openpyxl.styles import Font, PatternFill

    wb = load_workbook(WORKBOOK_PATH)
    today = dt.date.today().isoformat()
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="0CA30C")  # green, to visually mark tracker-added columns

    price_cache = {}
    updated_cells = 0
    tables_seen = 0

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        for table in _find_tables(ws):
            last_header = ws.cell(row=table["header_row"], column=table["last_col"]).value
            if last_header == today:
                # Already tracked today (a second run this same day) —
                # overwrite that column in place instead of duplicating it,
                # comparing against whatever was the checkpoint BEFORE it.
                target_col = table["last_col"]
                ref_col = _reference_price_col(ws, {**table, "last_col": target_col - 1})
            else:
                target_col = table["last_col"] + 1
                ref_col = _reference_price_col(ws, table)
            tables_seen += 1

            header_cell = ws.cell(row=table["header_row"], column=target_col, value=today)
            header_cell.font, header_cell.fill = header_font, header_fill

            for row in range(table["data_start"], table["data_end"] + 1):
                symbol = ws.cell(row=row, column=1).value
                if not symbol:
                    continue
                current = _real_price(symbol, price_cache)
                if current is None:
                    ws.cell(row=row, column=target_col, value="n/a")
                    continue

                ref_price = _parse_price(ws.cell(row=row, column=ref_col).value) if ref_col else None
                if ref_price:
                    pct = (current - ref_price) / ref_price * 100
                    ws.cell(row=row, column=target_col, value=f"₹{current:,.2f} ({pct:+.1f}%)")
                else:
                    ws.cell(row=row, column=target_col, value=f"₹{current:,.2f}")
                updated_cells += 1

    wb.save(WORKBOOK_PATH)
    print(f"[tracker] {today}: updated {updated_cells} cells across "
          f"{tables_seen} table(s) in {len(wb.sheetnames)} sheet(s) -> {WORKBOOK_PATH}")


if __name__ == "__main__":
    run()
