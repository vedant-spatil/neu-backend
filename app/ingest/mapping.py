"""Column mapping for uploaded CSVs (ETL stage 2). Any CSV can be loaded once each schema field is mapped to a column.

A header is matched to a schema field automatically when the names are equal after lowercasing and removing "_",
"-" and spaces (so "From Account", "from_account" and "FROM-ACCOUNT" all match). Any other header is mapped by hand
in the UI. The loader reads the mapped columns and returns the same frame as app.ingest.alerts.load_csv.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# Canonical schema: field -> required. These are the names the loader and the rules use.
SCHEMA: dict[str, bool] = {
    "timestamp": True,
    "from_account": True,
    "to_account": True,
    "amount_received": True,
    "amount_paid": True,
    "receiving_currency": True,
    "payment_currency": True,
    "payment_format": True,
    "from_bank": False,
    "to_bank": False,
    "is_laundering": False,
}
CHUNK = 250_000
LABEL_VALUES = {"0", "1", "true", "false"}


def normalize(name: str) -> str:
    """Lowercase, and drop "_", "-" and spaces. Other characters (such as the "." in pandas' "Account.1") are kept."""
    return re.sub(r"[_\- ]", "", name).lower()


def read_header(path: Path | str) -> list[str]:
    return pd.read_csv(path, nrows=0).columns.tolist()


def auto_map(header: list[str]) -> dict[str, str]:
    """schema field -> CSV column, for every field whose normalized name matches a header."""
    by_name: dict[str, str] = {}
    for col in header:
        by_name.setdefault(normalize(col), col)
    return {field: by_name[normalize(field)] for field in SCHEMA if normalize(field) in by_name}


def mapping_problems(mapping: dict[str, str], header: list[str]) -> list[str]:
    """Empty when the mapping can be used: all required fields mapped, no unknown fields, every column exists."""
    problems = [f"missing required field: {f}" for f, req in SCHEMA.items() if req and f not in mapping]
    problems += [f"unknown field: {f}" for f in mapping if f not in SCHEMA]
    problems += [f"column not in file: {c}" for c in mapping.values() if c not in header]
    return problems


def _chunks(path: Path | str, mapping: dict[str, str]):
    """Mapped columns, renamed to schema fields, in chunks. Everything is read as text; types are set later."""
    cols = list(mapping.values())
    for chunk in pd.read_csv(path, usecols=cols, chunksize=CHUNK, dtype=str, keep_default_na=False):
        yield chunk.rename(columns={col: field for field, col in mapping.items()})


def validate_mapped(path: Path | str, mapping: dict[str, str]) -> dict:
    """Row checks in chunks. Counts problems per type; never returns raw rows."""
    errors = {"bad_timestamp": 0, "bad_amount": 0, "null_account": 0, "bad_label": 0}
    report: dict = {"rows": 0, "non_usd_rows": 0}
    for chunk in _chunks(path, mapping):
        report["rows"] += len(chunk)
        errors["bad_timestamp"] += int(pd.to_datetime(chunk["timestamp"], errors="coerce").isna().sum())
        for col in ("amount_received", "amount_paid"):
            errors["bad_amount"] += int(pd.to_numeric(chunk[col], errors="coerce").isna().sum())
        errors["null_account"] += int((chunk["from_account"].str.strip() == "").sum())
        errors["null_account"] += int((chunk["to_account"].str.strip() == "").sum())
        if "is_laundering" in chunk:
            errors["bad_label"] += int((~chunk["is_laundering"].str.strip().str.lower().isin(LABEL_VALUES)).sum())
        report["non_usd_rows"] += int(
            ((chunk["receiving_currency"] != "US Dollar") | (chunk["payment_currency"] != "US Dollar")).sum()
        )
    report["errors"] = {k: v for k, v in errors.items() if v}
    return report


def load_mapped(path: Path | str, mapping: dict[str, str]) -> pd.DataFrame:
    """The transactions as app.ingest.alerts.load_csv returns them: ts, txn_id, from/to account and bank,
    amounts, currencies, payment format and is_laundering. Optional fields that are not mapped are left empty."""
    df = pd.concat(_chunks(path, mapping), ignore_index=True)
    label = (df["is_laundering"].str.strip().str.lower().isin(["1", "true"])
             if "is_laundering" in df else False)
    out = pd.DataFrame({
        "ts": pd.to_datetime(df["timestamp"]),
        "from_bank": df.get("from_bank", ""),
        "from_account": df["from_account"].str.strip(),
        "to_bank": df.get("to_bank", ""),
        "to_account": df["to_account"].str.strip(),
        "amount_received": pd.to_numeric(df["amount_received"]).astype(float),
        "receiving_currency": df["receiving_currency"],
        "amount_paid": pd.to_numeric(df["amount_paid"]).astype(float),
        "payment_currency": df["payment_currency"],
        "payment_format": df["payment_format"],
        "is_laundering": label,
    })
    out["txn_id"] = out.index + 1  # 1-based row id, as in load_csv
    return out
