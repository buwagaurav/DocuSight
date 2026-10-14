"""Structured-data RAG: every table we find (Excel sheets, CSVs, PDF tables)
lands in a per-document DuckDB file so the agent can answer aggregate /
numeric questions with exact SQL instead of guessing from text chunks
(Text-to-SQL routing)."""
from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

_FORBIDDEN = re.compile(
    r"\b(attach|detach|copy|export|import|install|load|pragma|set|create|insert|update|delete|drop|alter|"
    r"call|checkpoint|vacuum|read_csv\w*|read_parquet|read_json\w*|read_text|read_blob|glob)\b",
    re.I,
)


# currency symbols / ISO codes, thousands separators and spaces stripped before numeric parsing ("EUR 1,234.50")
_CURRENCY = r"(?i)\b(?:usd|eur|gbp|inr|jpy|cny|chf|cad|aud|rs)\b\.?|[$€£₹¥,\s]"


def sanitize(name: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-zA-Z0-9]+", "_", str(name)).strip("_").lower() or "table"
    if base[0].isdigit():
        base = f"t_{base}"
    base = base[:48]
    cand, i = base, 2
    while cand in taken:
        cand, i = f"{base}_{i}", i + 1
    taken.add(cand)
    return cand


def clean_columns(cols: list[Any]) -> list[str]:
    seen: set[str] = set()
    out = []
    for i, c in enumerate(cols):
        name = str(c).strip() if c is not None and str(c).strip() and str(c) != "nan" else f"col_{i + 1}"
        name = re.sub(r"\s+", " ", name)[:64]
        cand, k = name, 2
        while cand.lower() in seen:
            cand, k = f"{name}_{k}", k + 1
        seen.add(cand.lower())
        out.append(cand)
    return out


def coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    """Best-effort numeric/date coercion for messy spreadsheet columns
    ("$1,234.50", "12%", "(300)" accounting negatives, ...)."""
    df = df.copy()
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s) or pd.api.types.is_bool_dtype(s):
            continue
        s = s.astype(object).where(s.notna(), None)
        nonnull = s.dropna()
        nonnull = nonnull[nonnull.astype(str).str.strip() != ""]
        if nonnull.empty:
            continue
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in nonnull):
            df[col] = pd.to_numeric(s, errors="coerce")
            continue
        txt = nonnull.astype(str).str.strip()
        cleaned = (txt.str.replace(_CURRENCY, "", regex=True)
                   .str.replace(r"^\((.*)\)$", r"-\1", regex=True)
                   .str.replace(r"%$", "", regex=True))
        num = pd.to_numeric(cleaned, errors="coerce")
        if num.notna().mean() >= 0.9:
            full = (s.astype(str).str.strip()
                    .str.replace(_CURRENCY, "", regex=True)
                    .str.replace(r"^\((.*)\)$", r"-\1", regex=True)
                    .str.replace(r"%$", "", regex=True))
            df[col] = pd.to_numeric(full.replace({"": None, "None": None, "nan": None}), errors="coerce")
            continue
        if any(isinstance(v, (pd.Timestamp,)) or hasattr(v, "isoformat") for v in nonnull[:20]):
            df[col] = pd.to_datetime(s, errors="coerce")
            continue
        df[col] = s.map(lambda v: None if v is None or (isinstance(v, float) and math.isnan(v)) else str(v))
    return df


def write_tables(db_path: Path, tables: list[dict[str, Any]]) -> None:
    """tables: [{name, df, ...}]  -> one DuckDB table each."""
    if not tables:
        return
    con = duckdb.connect(str(db_path))
    try:
        for t in tables:
            df = t["df"]
            con.register("_tmp_df", df)
            con.execute(f'CREATE OR REPLACE TABLE "{t["name"]}" AS SELECT * FROM _tmp_df')
            con.unregister("_tmp_df")
    finally:
        con.close()


def describe_df(df: pd.DataFrame) -> list[dict[str, Any]]:
    cols = []
    for c in df.columns:
        s = df[c]
        info: dict[str, Any] = {"name": str(c), "type": str(s.dtype), "non_null": int(s.notna().sum())}
        if pd.api.types.is_bool_dtype(s):
            info.update(distinct=int(s.nunique()), top=[str(v) for v in s.dropna().unique()[:5]])
        elif pd.api.types.is_numeric_dtype(s) and s.notna().any():
            info.update(min=_num(s.min()), max=_num(s.max()), mean=_num(s.mean()), sum=_num(s.sum()))
        elif pd.api.types.is_datetime64_any_dtype(s) and s.notna().any():
            info.update(start=str(s.min())[:10], end=str(s.max())[:10])
        elif s.notna().any():
            vc = s.dropna().astype(str).value_counts()
            info.update(distinct=int(vc.size), top=[str(v) for v in vc.index[:5]])
        cols.append(info)
    return cols


def _num(v: Any) -> float | int | None:
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return int(f) if f.is_integer() else round(f, 4)
    except (TypeError, ValueError):
        return None


def _jsonable(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float):
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(v, (int, str, bool)):
        return v
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


def run_sql(db_path: Path, sql: str, limit: int = 200) -> dict[str, Any]:
    """Execute a single read-only SELECT against the document's tables."""
    q = sql.strip().rstrip(";").strip()
    if ";" in q:
        raise ValueError("Only a single statement is allowed.")
    if not re.match(r"^(select|with)\b", q, re.I):
        raise ValueError("Only SELECT / WITH queries are allowed.")
    # check keywords outside quoted identifiers / string literals ("Load", 'Set A' are fine)
    if _FORBIDDEN.search(re.sub(r'"[^"]*"|\'[^\']*\'', " ", q)):
        raise ValueError("Query uses a disallowed keyword or function.")
    if not db_path.exists():
        raise ValueError("This document has no tables.")
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        con.execute("SET enable_external_access = false")
        cur = con.execute(f"SELECT * FROM ({q}) AS _q LIMIT {limit + 1}")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
    finally:
        con.close()
    truncated = len(rows) > limit
    return {
        "columns": cols,
        "rows": [[_jsonable(v) for v in r] for r in rows[:limit]],
        "row_count": min(len(rows), limit),
        "truncated": truncated,
    }


def preview(db_path: Path, table: str, limit: int = 500, offset: int = 0) -> dict[str, Any]:
    if not re.fullmatch(r"[a-z0-9_]+", table):
        raise ValueError("bad table name")
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        total = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
        cur = con.execute(f'SELECT * FROM "{table}" LIMIT {int(limit)} OFFSET {int(offset)}')
        cols = [d[0] for d in cur.description]
        rows = [[_jsonable(v) for v in r] for r in cur.fetchall()]
    finally:
        con.close()
    return {"columns": cols, "rows": rows, "total": total}
