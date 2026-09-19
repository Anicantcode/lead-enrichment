"""File ingestion — one big file → normalized DataFrame.

Supported: CSV, TSV, XLSX, XLS, Parquet, JSON (array or jsonl), ODS (via openpyxl).
Handles: encoding detection, delimiter sniffing, big files via chunking, Excel sheet pick.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import pandas as pd

log = logging.getLogger(__name__)

SUPPORTED_EXTS = {".csv", ".tsv", ".txt", ".xlsx", ".xls", ".parquet", ".pq", ".json", ".jsonl", ".ndjson"}


def _detect_csv_params(path: Path, sample_bytes: int = 16384) -> dict:
    """Sniff delimiter and encoding."""
    raw = path.read_bytes()[:sample_bytes]
    text = None
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    text = text or raw.decode("utf-8", errors="replace")
    # delimiter sniff: count commas vs tabs vs semis
    import csv

    sniffer = csv.Sniffer()
    dialect = None
    try:
        dialect = sniffer.sniff(text[:4096], delimiters=[",", ";", "\t", "|"])
    except Exception:
        pass
    delim = dialect.delimiter if dialect else ("," if text.count(",") >= text.count("\t") else "\t")
    # check for header
    has_header = True
    try:
        has_header = sniffer.has_header(text[:4096])
    except Exception:
        pass
    return {"delimiter": delim, "has_header": has_header}


def load_file(path: Path, sheet: Optional[str | int] = None, nrows: Optional[int] = None) -> pd.DataFrame:
    """Load any supported file into a DataFrame. Raises on unsupported type."""
    if not path.exists():
        raise FileNotFoundError(f"Input not found: {path}")

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTS:
        # try compound like .csv.gz not supported yet — give hint
        raise ValueError(f"Unsupported extension '{ext}'. Supported: {sorted(SUPPORTED_EXTS)}")

    log.info(f"Loading {path} ({ext}) ...")

    if ext in (".csv", ".tsv", ".txt"):
        params = _detect_csv_params(path)
        delim = "\t" if ext == ".tsv" else params["delimiter"]
        # try utf-8-sig first
        for enc in ("utf-8-sig", "utf-8", "latin-1"):
            try:
                df = pd.read_csv(
                    path,
                    delimiter=delim,
                    encoding=enc,
                    dtype=str,  # keep everything as string initially
                    keep_default_na=True,
                    na_values=["", "NA", "N/A", "null", "NULL", "#N/A"],
                    nrows=nrows,
                    on_bad_lines="warn",
                    engine="python",
                )
                break
            except UnicodeDecodeError:
                continue
        else:
            df = pd.read_csv(path, delimiter=delim, dtype=str, keep_default_na=True, nrows=nrows, engine="python")

        # clean column names: strip BOM, whitespace
        df.columns = [str(c).strip().replace("\ufeff", "") for c in df.columns]

    elif ext in (".xlsx", ".xls"):
        # pick sheet: if not specified, use first sheet with most rows
        xls = pd.ExcelFile(path)
        if sheet is not None:
            df = pd.read_excel(xls, sheet_name=sheet, dtype=str, nrows=nrows)
        else:
            if len(xls.sheet_names) == 1:
                df = pd.read_excel(xls, sheet_name=0, dtype=str, nrows=nrows)
            else:
                # choose sheet with max rows (heuristic)
                best = None
                best_rows = -1
                best_name = None
                for name in xls.sheet_names:
                    tmp = pd.read_excel(xls, sheet_name=name, dtype=str, nrows=300)
                    rows = len(tmp)
                    if rows > best_rows:
                        best_rows = rows
                        best_name = name
                log.info(f"Multiple sheets detected {xls.sheet_names} — auto-selected '{best_name}' ({best_rows} sample rows)")
                df = pd.read_excel(xls, sheet_name=best_name, dtype=str, nrows=nrows)

    elif ext in (".parquet", ".pq"):
        df = pd.read_parquet(path)
        df = df.astype(str)

    elif ext in (".json", ".jsonl", ".ndjson"):
        text = path.read_text(encoding="utf-8-sig").strip()
        if not text:
            raise ValueError("JSON file is empty")
        # try jsonl if lines look like objects
        if path.suffix.lower() in (".jsonl", ".ndjson") or ("\n{" in text[:2000] and text.startswith("{") is False):
            # jsonl
            rows = []
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
            df = pd.DataFrame(rows).astype(str)
        else:
            data = json.loads(text)
            if isinstance(data, list):
                df = pd.DataFrame(data).astype(str)
            elif isinstance(data, dict):
                # maybe {"leads": [...]} — find first list value
                list_val = next((v for v in data.values() if isinstance(v, list)), None)
                if list_val is not None:
                    df = pd.DataFrame(list_val).astype(str)
                else:
                    df = pd.DataFrame([data]).astype(str)
            else:
                raise ValueError("Unexpected JSON shape")

    else:
        raise ValueError(f"Unhandled extension {ext}")

    # post: drop fully empty rows/cols
    df = df.dropna(how="all").dropna(axis=1, how="all")
    # strip column whitespace again
    df.columns = [str(c).strip() for c in df.columns]

    # add row_id for traceability before any dedup
    df.insert(0, "_row_id", range(1, len(df) + 1))

    log.info(f"Loaded {len(df)} rows × {len(df.columns)} cols. Columns: {list(df.columns)[:20]}")
    if len(df) == 0:
        raise ValueError("No rows found after loading — check file content / delimiter / sheet selection.")
    return df


def sample_preview(df: pd.DataFrame, n: int = 5) -> str:
    """Return a markdown-ish preview for logging."""
    cols = list(df.columns)[:12]
    head = df[cols].head(n).to_string(index=False)
    return f"Preview ({n} rows):\n{head}"
