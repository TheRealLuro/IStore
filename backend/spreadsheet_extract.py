"""Server-side spreadsheet → normalized-JSON extraction.

Powers `GET /images/{id}/spreadsheet`, which the in-app spreadsheet
viewer (frontend/neuthek/src/spreadsheet-viewer.jsx) hits to render the
ACTUAL cells of a workbook as a live grid — sheet tabs, column letters,
row numbers — instead of falling back to a download link.

Why a server parse instead of a JS lib
---------------------------------------
The viewer used to parse `.xlsx` client-side with exceljs, but exceljs
only reads the modern zip-based OOXML — legacy binary `.xls` (BIFF) and
OpenDocument `.ods` threw and dropped the user to a "can't render,
download instead" dead-end. Rather than ship a second risky JS parser
for those, we parse ALL three formats here with **python-calamine**
(a maintained Rust `calamine` binding, MIT/Apache-2.0). One library,
one code path, native typed values (numbers, dates, booleans), and no
LibreOffice subprocess (LibreOffice isn't even in the API image — it
lives in the ml-worker). calamine ships manylinux wheels, so it adds no
build-time system deps.

Output shape (kept deliberately dumb so the FE just renders it)
---------------------------------------------------------------
    {
      "sheets": [
        {
          "name": "Sheet1",
          "rows": [ [cell, cell, …], … ],   # row-major, padded to n_cols
          "n_rows": <int>,                  # TRUE row count (pre-clip)
          "n_cols": <int>,                  # TRUE col count (pre-clip)
          "shown_rows": <int>,              # rows actually in `rows`
          "shown_cols": <int>,              # cols actually in `rows`
          "truncated": <bool>               # clipped on either axis
        },
        …
      ]
    }

Each `cell` is `{ "v": <display string>, "t": <type tag> }` where the
type tag is one of: "s" (string/text), "n" (number), "d" (date /
datetime / time / duration), "b" (boolean), "e" (error), "" (empty).
The FE uses the tag for alignment (numbers/dates right) and styling;
the value is already formatted for display so the FE never re-derives
Excel serial dates or number masks.
"""

from __future__ import annotations

import datetime as _dt
import logging
from io import BytesIO
from typing import Any

logger = logging.getLogger(__name__)

# Hard caps so a giant workbook can't blow up the JSON response (or the
# browser that renders it). Per sheet we emit at most MAX_ROWS used rows
# × MAX_COLS used columns; anything past that is dropped and `truncated`
# flips true so the FE can show a "showing first N" banner.
MAX_ROWS = 1000
MAX_COLS = 100
# A workbook can carry an absurd number of (mostly empty) sheets; cap so
# the response stays bounded. The viewer shows a tab per emitted sheet.
MAX_SHEETS = 50


class SpreadsheetParseError(Exception):
    """Raised when the bytes can't be parsed as any supported workbook
    format. The endpoint maps this to a 422 so the FE shows a clean
    "couldn't read this spreadsheet" state (and the download link)."""


def _trim_number(x: float) -> str:
    """Render a float without trailing-zero / float-noise cruft.

    Integers-as-floats (1875.0) render as "1875"; genuine fractions keep
    up to 10 significant decimals with trailing zeros stripped so
    3.10 → "3.1" but 1234.5 stays "1234.5". We intentionally do NOT add
    thousands separators here — the FE applies locale grouping so the
    grouping char matches the viewer's locale, not the server's.
    """
    if x != x or x in (float("inf"), float("-inf")):  # NaN / inf
        return str(x)
    if float(x).is_integer():
        # Avoid "1e+16"-style sci-notation for big whole numbers.
        return str(int(x))
    # `repr` gives the shortest round-trippable form in 3.1+; good enough
    # and far cleaner than a fixed %.10f that pads zeros.
    s = repr(float(x))
    return s


def _fmt_date(d: _dt.date) -> str:
    return d.isoformat()


def _fmt_datetime(d: _dt.datetime) -> str:
    # Drop a midnight time component so a pure date stored as datetime
    # doesn't render "2026-03-15 00:00:00".
    if d.hour == 0 and d.minute == 0 and d.second == 0 and d.microsecond == 0:
        return d.date().isoformat()
    # Seconds only when non-zero — keeps the common HH:MM case tidy.
    if d.second == 0 and d.microsecond == 0:
        return d.strftime("%Y-%m-%d %H:%M")
    return d.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_time(t: _dt.time) -> str:
    if t.second == 0 and t.microsecond == 0:
        return t.strftime("%H:%M")
    return t.strftime("%H:%M:%S")


def _fmt_timedelta(td: _dt.timedelta) -> str:
    """Excel/ODS 'duration' cells come back as timedelta. Render as
    [H]:MM:SS so a 25-hour duration reads "25:30:00", not "1 day,
    1:30:00"."""
    total = int(td.total_seconds())
    sign = "-" if total < 0 else ""
    total = abs(total)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{sign}{h}:{m:02d}:{s:02d}"


def _cell(value: Any) -> dict[str, str]:
    """Normalize one calamine cell value into `{v, t}` (see module
    docstring). calamine hands back native Python types, so this is a
    type switch — no Excel-serial / numFmt decoding needed."""
    if value is None or value == "":
        return {"v": "", "t": ""}
    # bool is a subclass of int — test it FIRST so True/False don't fall
    # into the numeric branch and render as "1"/"0".
    if isinstance(value, bool):
        return {"v": "TRUE" if value else "FALSE", "t": "b"}
    if isinstance(value, (int,)):
        return {"v": str(value), "t": "n"}
    if isinstance(value, float):
        return {"v": _trim_number(value), "t": "n"}
    if isinstance(value, _dt.datetime):
        return {"v": _fmt_datetime(value), "t": "d"}
    if isinstance(value, _dt.date):
        return {"v": _fmt_date(value), "t": "d"}
    if isinstance(value, _dt.time):
        return {"v": _fmt_time(value), "t": "d"}
    if isinstance(value, _dt.timedelta):
        return {"v": _fmt_timedelta(value), "t": "d"}
    # Strings and anything unexpected → text. calamine may surface a
    # formula error as a string like "#DIV/0!"; tag those as errors so
    # the FE can center + dim them.
    s = str(value)
    if s.startswith("#") and s.endswith(("!", "?")) and len(s) <= 12:
        return {"v": s, "t": "e"}
    return {"v": s, "t": "s"}


def _build_sheet(name: str, raw_rows: list[list[Any]]) -> dict:
    """Clip + normalize one sheet's raw row matrix.

    `raw_rows` is calamine's `sheet.to_python()` output: a list of rows,
    each a list of native-typed cell values. calamine already trims
    fully-trailing-empty rows/cols at the sheet edge, but rows can be
    ragged (a short row when later rows are wider), so we compute the
    true width across all rows and pad every emitted row to it.
    """
    n_rows = len(raw_rows)
    n_cols = max((len(r) for r in raw_rows), default=0)

    shown_rows = min(n_rows, MAX_ROWS)
    shown_cols = min(n_cols, MAX_COLS)

    out_rows: list[list[dict]] = []
    for r in range(shown_rows):
        src = raw_rows[r]
        row_out = [None] * shown_cols
        for c in range(shown_cols):
            row_out[c] = _cell(src[c]) if c < len(src) else {"v": "", "t": ""}
        out_rows.append(row_out)

    return {
        "name": name,
        "rows": out_rows,
        "n_rows": n_rows,
        "n_cols": n_cols,
        "shown_rows": shown_rows,
        "shown_cols": shown_cols,
        "truncated": (n_rows > shown_rows) or (n_cols > shown_cols),
    }


def extract_workbook(data: bytes) -> dict:
    """Parse workbook bytes (.xlsx / .xls / .ods / .xlsb / .csv) into the
    normalized JSON dict described in the module docstring.

    Raises `SpreadsheetParseError` if calamine can't read the bytes as
    any supported format (corrupt file, wrong type, password-protected).
    Runs CPU-bound work, so callers should invoke via
    `asyncio.to_thread` to keep the event loop free.
    """
    try:
        import python_calamine
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise SpreadsheetParseError(
            "python-calamine is not installed in this image"
        ) from exc

    try:
        workbook = python_calamine.load_workbook(BytesIO(data))
        sheet_names = list(workbook.sheet_names)
    except Exception as exc:
        # calamine raises a variety of native errors (Xls/Ods/Xlsx/Zip);
        # collapse them all to our sentinel so the endpoint can 422.
        raise SpreadsheetParseError(
            f"Could not parse spreadsheet: {exc}"
        ) from exc

    sheets: list[dict] = []
    for name in sheet_names[:MAX_SHEETS]:
        try:
            sheet = workbook.get_sheet_by_name(name)
            raw_rows = sheet.to_python()
        except Exception:
            # One unreadable sheet shouldn't sink the whole workbook —
            # emit it as empty and keep going.
            logger.warning("spreadsheet: sheet %r failed to read", name, exc_info=True)
            sheets.append({
                "name": name,
                "rows": [],
                "n_rows": 0,
                "n_cols": 0,
                "shown_rows": 0,
                "shown_cols": 0,
                "truncated": False,
            })
            continue
        sheets.append(_build_sheet(name, raw_rows))

    if not sheets:
        raise SpreadsheetParseError("Workbook has no readable sheets")

    return {
        "sheets": sheets,
        "sheet_count": len(sheet_names),
        "sheets_truncated": len(sheet_names) > MAX_SHEETS,
    }
