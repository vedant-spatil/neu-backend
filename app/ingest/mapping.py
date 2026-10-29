"""Column mapping for uploaded CSVs (ETL stage 2). Any CSV can be loaded once each schema field is mapped to a column.

A header is matched to a schema field automatically when the names are equal after lowercasing and removing "_",
"-" and spaces (so "From Account", "from_account" and "FROM-ACCOUNT" all match). Two-column account layouts such as
HI-Small (sender "Account", receiver "Account.1" after pandas renames the duplicate) are matched through ALIASES.
Any other header is mapped by hand in the UI. The loader reads the mapped columns and returns the same frame as
app.ingest.alerts.load_csv, with the text columns stored as categories so a full sync fits in a small instance.
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
# Normalized header names that stand for a field when the plain name does not match. HI-Small has two "Account"
# headers: pandas keeps the first as "Account" (sender) and renames the second to "Account.1" (receiver).
ALIASES: dict[str, tuple[str, ...]] = {
    "from_account": ("account",),
    "to_account": ("account.1",),
}
# Text fields with few distinct values or repeated account ids. They are stored as categories (see load_mapped).
CATEGORICAL = ("from_account", "to_account", "from_bank", "to_bank", "receiving_currency", "payment_currency",
               "payment_format")
CHUNK = 250_000
LABEL_VALUES = {"0", "1", "true", "false"}


def normalize(name: str) -> str:
    """Lowercase, and drop "_", "-" and spaces. Other characters (such as the "." in pandas' "Account.1") are kept."""
    return re.sub(r"[_\- ]", "", name).lower()


def read_header(path: Path | str) -> list[str]:
    return pd.read_csv(path, nrows=0).columns.tolist()


def auto_map(header: list[str]) -> dict[str, str]:
    """schema field -> CSV column, for every field whose normalized name (or alias) matches a header."""
    by_name: dict[str, str] = {}
    for col in header:
        by_name.setdefault(normalize(col), col)
    mapping: dict[str, str] = {}
    for field in SCHEMA:
        for name in (normalize(field), *ALIASES.get(field, ())):
            if name in by_name and by_name[name] not in mapping.values():
                mapping[field] = by_name[name]
                break
    return mapping


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


def _clean(field: str, values: pd.Series) -> pd.Series:
    """Account ids are stripped; other text is kept as the file has it."""
    return values.str.strip() if field in ("from_account", "to_account") else values


def _categories(path: Path | str, mapping: dict[str, str]) -> dict[str, list[str]]:
    """The distinct values of each mapped categorical field, from a first pass that keeps only those columns."""
    fields = [f for f in CATEGORICAL if f in mapping]
    seen: dict[str, set[str]] = {f: set() for f in fields}
    cols = [mapping[f] for f in fields]
    if not cols:
        return seen
    for chunk in pd.read_csv(path, usecols=cols, chunksize=CHUNK, dtype=str, keep_default_na=False):
        for field in fields:
            seen[field].update(_clean(field, chunk[mapping[field]]).unique())
    return {f: sorted(values) for f, values in seen.items()}


def _typed(chunk: pd.DataFrame, cats: dict[str, list[str]]) -> pd.DataFrame:
    """One chunk in the loader's types. Categorical fields use the shared category list, so chunks concatenate
    without falling back to plain strings."""
    def text(field: str):
        if field not in chunk:
            return ""  # not mapped: left empty, as before
        values = _clean(field, chunk[field])
        if field in cats:
            return pd.Categorical(values, categories=cats[field])
        return values

    return pd.DataFrame({
        "ts": pd.to_datetime(chunk["timestamp"]),
        "from_bank": text("from_bank"),
        "from_account": text("from_account"),
        "to_bank": text("to_bank"),
        "to_account": text("to_account"),
        "amount_received": pd.to_numeric(chunk["amount_received"]).astype(float),
        "receiving_currency": text("receiving_currency"),
        "amount_paid": pd.to_numeric(chunk["amount_paid"]).astype(float),
        "payment_currency": text("payment_currency"),
        "payment_format": text("payment_format"),
        "is_laundering": (chunk["is_laundering"].str.strip().str.lower().isin(["1", "true"])
                          if "is_laundering" in chunk else False),
    })


def load_mapped(path: Path | str, mapping: dict[str, str]) -> pd.DataFrame:
    """The transactions as app.ingest.alerts.load_csv returns them: ts, txn_id, from/to account and bank,
    amounts, currencies, payment format and is_laundering. Optional fields that are not mapped are left empty.

    Text columns become categories with one shared category list (two passes over the mapped text columns). Account
    ids repeat across rows and bank and currency names have a handful of values, so this is far smaller than strings.
    """
    cats = _categories(path, mapping)
    parts = [_typed(chunk, cats) for chunk in _chunks(path, mapping)]
    df = pd.concat(parts, ignore_index=True)
    df["txn_id"] = df.index + 1  # 1-based row id, as in load_csv
    return df
