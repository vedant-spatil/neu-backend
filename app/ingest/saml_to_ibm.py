"""Map SAML-D to the IBM HI-Small schema used by Multi-GNN (external/Multi-GNN).

Writes formatted_transactions.csv with the columns format_kaggle_files.py produces:
  EdgeID,from_id,to_id,Timestamp,Amount Sent,Sent Currency,Amount Received,Received Currency,
  Payment Format,Is Laundering
Timestamp is seconds since the first day's midnight minus 10 s, as in format_kaggle_files.py.

Known gaps (documented, not hidden):
  - SAML-D has no bank IDs. Sender/Receiver bank LOCATION stands in for the IBM bank code.
  - SAML-D has one Amount in the payment currency. No FX rates are given, so Amount Received
    is set equal to Amount Sent. Received-currency amounts are not reconstructed.
  - Debit card has no IBM payment format. It maps to Credit Card.
  - Laundering_type is written to a side file (pattern labels for evaluation only; not a model input).

Usage: python -m app.ingest.saml_to_ibm --csv data/SAML-D.csv --out external/data/SAML-D
"""

import argparse
import os
import time

import numpy as np
import pandas as pd

# SAML-D Payment_type -> IBM Payment Format label (IBM names from HI-Small; see format_kaggle_files.py)
PAYMENT_MAP = {
    "ACH": "ACH",
    "Cheque": "Cheque",
    "Credit card": "Credit Card",
    "Debit card": "Credit Card",
    "Cross-border": "Wire",
    "Cash Deposit": "Cash",
    "Cash Withdrawal": "Cash",
}
PAYMENT_CODE = {"ACH": 0, "Cheque": 1, "Credit Card": 2, "Wire": 3, "Cash": 4}


def convert(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    unknown = set(df["Payment_type"].unique()) - set(PAYMENT_MAP)
    if unknown:
        raise ValueError(f"unmapped Payment_type values: {sorted(unknown)}")

    ts = pd.to_datetime(df["Date"] + " " + df["Time"], format="%Y-%m-%d %H:%M:%S")
    day0 = ts.min().normalize()
    timestamp = (ts - day0).dt.total_seconds() + 10  # format_kaggle_files.py: firstTs = midnight - 10

    # Node id = (bank location, account). IBM uses (bank code, account); location is the closest stand-in.
    sender_key = df["Sender_bank_location"] + "|" + df["Sender_account"].astype(str)
    receiver_key = df["Receiver_bank_location"] + "|" + df["Receiver_account"].astype(str)
    codes, uniques = pd.factorize(pd.concat([sender_key, receiver_key], ignore_index=True))
    n = len(df)
    from_id = codes[:n]
    to_id = codes[n:]

    currencies = pd.concat([df["Payment_currency"], df["Received_currency"]], ignore_index=True)
    cur_codes, _ = pd.factorize(currencies)
    sent_cur = cur_codes[:n]
    recv_cur = cur_codes[n:]

    out = pd.DataFrame(
        {
            "EdgeID": np.arange(n),
            "from_id": from_id,
            "to_id": to_id,
            "Timestamp": timestamp.astype(np.int64),
            "Amount Sent": df["Amount"].astype(float),
            "Sent Currency": sent_cur,
            "Amount Received": df["Amount"].astype(float),  # no FX in SAML-D; see module docstring
            "Received Currency": recv_cur,
            "Payment Format": df["Payment_type"].map(PAYMENT_MAP).map(PAYMENT_CODE),
            "Is Laundering": df["Is_laundering"].astype(int),
        }
    )
    out["Laundering_type"] = df["Laundering_type"].values
    out = out.sort_values("Timestamp", kind="stable").reset_index(drop=True)
    out["EdgeID"] = np.arange(len(out))

    labels = out[["EdgeID", "Laundering_type"]].copy()
    out = out.drop(columns=["Laundering_type"])
    return out, labels


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    t0 = time.perf_counter()

    df = pd.read_csv(args.csv, dtype={"Sender_account": str, "Receiver_account": str})
    print(f"[saml] loaded rows={len(df)} elapsed={time.perf_counter() - t0:.0f}s", flush=True)

    out, labels = convert(df)
    os.makedirs(args.out, exist_ok=True)
    out.to_csv(os.path.join(args.out, "formatted_transactions.csv"), index=False)
    labels.to_csv(os.path.join(args.out, "laundering_types.csv"), index=False)
    print(
        f"[saml] written rows={len(out)} laundering={int(out['Is Laundering'].sum())} "
        f"nodes={int(max(out['from_id'].max(), out['to_id'].max()) + 1)} elapsed={time.perf_counter() - t0:.0f}s",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
