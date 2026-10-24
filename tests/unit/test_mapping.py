import pandas as pd

from app.ingest.alerts import load_csv
from app.ingest.mapping import (
    SCHEMA,
    auto_map,
    load_mapped,
    mapping_problems,
    normalize,
    validate_mapped,
)

HI_HEADER = ["Timestamp", "From Bank", "Account", "To Bank", "Account.1", "Amount Received", "Receiving Currency",
             "Amount Paid", "Payment Currency", "Payment Format", "Is Laundering"]
HI_MAPPING = {
    "timestamp": "Timestamp", "from_bank": "From Bank", "from_account": "Account", "to_bank": "To Bank",
    "to_account": "Account.1", "amount_received": "Amount Received", "receiving_currency": "Receiving Currency",
    "amount_paid": "Amount Paid", "payment_currency": "Payment Currency", "payment_format": "Payment Format",
    "is_laundering": "Is Laundering",
}


def _write_hi_csv(path):
    path.write_text(
        ",".join(HI_HEADER) + "\n"
        "2022/09/01 10:00,111,A1,222,B1,100.5,US Dollar,100.5,US Dollar,Cash,0\n"
        "2022/09/01 11:30,111,A1,333,B2,9000,US Dollar,9000,US Dollar,ACH,1\n"
        "2022/09/02 09:15,444,C1,222,B1,20,Euro,20,US Dollar,Cheque,0\n",
        encoding="utf-8",
    )
    return path


def test_normalize_drops_separators_and_case():
    assert normalize("From Account") == normalize("from_account") == normalize("FROM-ACCOUNT") == "fromaccount"
    assert normalize("Account.1") == "account.1"  # dots are kept


def test_auto_map_matches_names_and_leaves_the_rest_for_hand_mapping():
    auto = auto_map(HI_HEADER)
    assert auto["timestamp"] == "Timestamp"
    assert auto["amount_paid"] == "Amount Paid"
    assert auto["payment_format"] == "Payment Format"
    assert "from_account" not in auto  # "Account" does not match "from_account"
    assert "to_account" not in auto    # "Account.1" does not match "to_account"


def test_mapping_problems_reports_missing_required_and_bad_columns():
    assert mapping_problems(HI_MAPPING, HI_HEADER) == []
    partial = {k: v for k, v in HI_MAPPING.items() if k != "to_account"}
    assert mapping_problems(partial, HI_HEADER) == ["missing required field: to_account"]
    bad = {**HI_MAPPING, "amount_paid": "Nope"}
    assert mapping_problems(bad, HI_HEADER) == ["column not in file: Nope"]
    assert "unknown field: foo" in mapping_problems({**HI_MAPPING, "foo": "Timestamp"}, HI_HEADER)


def test_load_mapped_matches_load_csv(tmp_path):
    path = _write_hi_csv(tmp_path / "hi.csv")
    mapped = load_mapped(path, HI_MAPPING)
    reference = load_csv(str(path))
    for col in ("ts", "from_account", "to_account", "amount_received", "amount_paid", "receiving_currency",
                "payment_currency", "payment_format", "is_laundering", "txn_id"):
        assert list(mapped[col]) == list(reference[col]), col


def test_validate_mapped_counts_problems(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text(
        ",".join(HI_HEADER) + "\n"
        "2022/09/01 10:00,111,A1,222,B1,100,US Dollar,100,US Dollar,Cash,0\n"
        "not a date,111,A1,222,B1,xx,US Dollar,100,US Dollar,Cash,maybe\n"
        "2022/09/01 10:00,111,,222,B1,100,US Dollar,100,US Dollar,Cash,0\n",
        encoding="utf-8",
    )
    report = validate_mapped(path, HI_MAPPING)
    assert report["rows"] == 3
    assert report["errors"] == {"bad_timestamp": 1, "bad_amount": 1, "null_account": 1, "bad_label": 1}
    assert report["non_usd_rows"] == 0


def test_optional_fields_are_filled_when_unmapped(tmp_path):
    path = tmp_path / "min.csv"
    path.write_text(
        "date,src,dst,recv,paid,rc,pc,fmt\n"
        "2022-09-01 10:00:00,A1,B1,100,100,US Dollar,US Dollar,Cash\n",
        encoding="utf-8",
    )
    mapping = {"timestamp": "date", "from_account": "src", "to_account": "dst", "amount_received": "recv",
               "amount_paid": "paid", "receiving_currency": "rc", "payment_currency": "pc", "payment_format": "fmt"}
    assert mapping_problems(mapping, ["date", "src", "dst", "recv", "paid", "rc", "pc", "fmt"]) == []
    df = load_mapped(path, mapping)
    assert df.loc[0, "from_bank"] == "" and df.loc[0, "to_bank"] == ""
    assert bool(df.loc[0, "is_laundering"]) is False
    assert isinstance(df.loc[0, "ts"], pd.Timestamp)
    assert set(SCHEMA) - set(mapping) == {"from_bank", "to_bank", "is_laundering"}
