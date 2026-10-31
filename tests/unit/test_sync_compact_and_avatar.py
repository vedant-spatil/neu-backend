"""Sync memory and mapping (compact text columns), HI-Small aliases, the avatar type check, and one case per account."""

from pathlib import Path

import pandas as pd

from app.api.routers.auth import avatar_type_of
from app.ingest.alerts import build_alerts, load_csv
from app.ingest.mapping import auto_map, load_mapped

HI_SMALL_HEADER = ("Timestamp,From Bank,Account,To Bank,Account.1,Amount Received,Receiving Currency,"
                   "Amount Paid,Payment Currency,Payment Format,Is Laundering")


def _fan_out_csv(path: Path) -> Path:
    """Account A sends to ten different accounts within the hour (rule R2 fan-out), plus two unrelated transfers."""
    rows = [HI_SMALL_HEADER]
    for i in range(10):
        rows.append(f"2022/09/01 10:{i:02d},001,A-SRC,002,B-{i:02d},100.00,US Dollar,100.00,US Dollar,ACH,0")
    rows.append("2022/09/02 09:00,003,C-SRC,004,D-DST,50.00,US Dollar,50.00,US Dollar,Cheque,0")
    rows.append("2022/09/02 09:05,003,C-SRC,004,D-DST,60.00,US Dollar,60.00,US Dollar,Cheque,1")
    path.write_text("\n".join(rows) + "\n")
    return path


def test_hi_small_headers_map_to_sender_and_receiver() -> None:
    header = HI_SMALL_HEADER.split(",")
    mapping = auto_map(header)
    assert mapping["from_account"] == "Account"
    assert mapping["to_account"] == "Account.1"


def test_auto_map_does_not_reuse_one_column_for_two_fields() -> None:
    mapping = auto_map(["Account", "Account.1"])
    assert len(set(mapping.values())) == len(mapping)


def test_load_mapped_uses_categories_and_matches_values(tmp_path: Path) -> None:
    path = _fan_out_csv(tmp_path / "small.csv")
    df = load_mapped(path, auto_map(HI_SMALL_HEADER.split(",")))
    assert str(df["from_account"].dtype) == "category"
    assert str(df["to_account"].dtype) == "category"
    assert list(df["txn_id"]) == list(range(1, len(df) + 1))
    assert df["from_account"].iloc[0] == "A-SRC"
    assert df["is_laundering"].tolist()[-1] is True
    assert df["from_bank"].iloc[0] == "001"


def test_rules_give_the_same_alerts_on_compact_and_plain_frames(tmp_path: Path) -> None:
    """The compact (categorical) frame must produce exactly the alerts the original loader produces."""
    path = _fan_out_csv(tmp_path / "small.csv")
    plain = load_csv(str(path))
    compact = load_mapped(path, auto_map(HI_SMALL_HEADER.split(",")))

    def summary(df: pd.DataFrame) -> list[tuple]:
        return sorted((a.account_id, tuple(sorted(a.rules)), round(a.risk_score, 6)) for a in build_alerts(df))

    assert summary(compact) == summary(plain)
    assert any(rule == "R2_fan_out" for _, rules, _ in summary(plain) for rule in rules)


def test_avatar_accepts_png_and_jpeg_by_signature_only() -> None:
    assert avatar_type_of(b"\x89PNG\r\n\x1a\n" + b"rest") == "image/png"
    assert avatar_type_of(b"\xff\xd8\xff\xe0" + b"rest") == "image/jpeg"
    assert avatar_type_of(b"GIF89a....") is None
    assert avatar_type_of(b"<html>") is None
    assert avatar_type_of(b"") is None
