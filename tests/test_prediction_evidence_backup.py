from __future__ import annotations

import json
from pathlib import Path

import pytest

from prediction_engine.evidence_backup import (
    create_verified_backup,
    logical_snapshot,
    open_store_with_private_backup,
    restore_verified_backup,
)
from prediction_engine.engine import run_prediction_engine
from prediction_engine.storage import PredictionStore


def _row(symbol: str, market: str = "TW") -> dict:
    return {
        "symbol": symbol,
        "name": symbol,
        "market": market,
        "type": "個股",
        "price": 100.0,
        "official_close_price": 100.0,
        "official_session_date": "2026-01-02",
        "market_contract_valid": True,
        "market_data_quality_score": 95.0,
        "technical_score": 80.0,
        "volume_score": 75.0,
        "positioning_score": 70.0,
        "group_score": 72.0,
        "macro_score": 68.0,
        "fundamental_score": 76.0,
        "valuation_score": 65.0,
        "entry_score": 74.0,
        "news_data_available": False,
        "change_pct": 1.0,
        "rsi": 55.0,
    }


def _populated_store(tmp_path: Path) -> PredictionStore:
    reports = tmp_path / "reports"
    reports.mkdir()
    database = tmp_path / "source" / "prediction_engine.sqlite3"
    rows = [_row("2330.TW"), _row("NVDA", "US")]
    run_prediction_engine(
        reports,
        rows,
        period="test",
        updated_at="2026-01-02T12:00:00Z",
        intraday=False,
        db_path=database,
    )
    store = PredictionStore(database)
    with store.connect() as db:
        db.execute(
            """INSERT INTO unit_learning_predictions(
            unit_id,objective,market,asset_group,session_date,symbol,name,
            source_price,direction,strength,confidence,evidence_status,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "technical", "direction", "TW", "TW_STOCK", "2026-01-02",
                "2330.TW", "台積電", 100.0, 1, 70.0, 65.0, "available",
                "2026-01-02T12:00:00Z",
            ),
        )
    return store


def test_private_prediction_evidence_backup_round_trip_is_exact(tmp_path: Path) -> None:
    source = _populated_store(tmp_path)
    backup = tmp_path / "private" / "prediction_evidence_backup.json.gz"
    manifest = create_verified_backup(
        source, backup, created_at="2026-01-02T12:30:00Z"
    )
    assert manifest["verified"] is True
    assert manifest["compressed_bytes"] < manifest["raw_bytes"]
    assert manifest["table_counts"]["predictions"] > 0
    assert manifest["table_counts"]["unit_learning_predictions"] == 1

    restored = PredictionStore(tmp_path / "restored" / "prediction_engine.sqlite3")
    result = restore_verified_backup(restored, backup)
    assert result["integrity_check"] == "ok"
    assert logical_snapshot(restored) == logical_snapshot(source)


def test_private_backup_recovers_only_an_empty_store(tmp_path: Path) -> None:
    source = _populated_store(tmp_path)
    recovery_dir = tmp_path / "recovery"
    backup = recovery_dir / "prediction_evidence_backup.json.gz"
    create_verified_backup(source, backup, created_at="2026-01-02T12:30:00Z")

    restored, status = open_store_with_private_backup(
        recovery_dir / "prediction_engine.sqlite3", max_bytes=500 * 1024 * 1024
    )
    assert status["attempted"] is True
    assert status["restored"] is True
    assert logical_snapshot(restored) == logical_snapshot(source)

    _, second = open_store_with_private_backup(
        recovery_dir / "prediction_engine.sqlite3", max_bytes=500 * 1024 * 1024
    )
    assert second == {"attempted": False, "restored": False}


def test_private_backup_rejects_checksum_tampering(tmp_path: Path) -> None:
    source = _populated_store(tmp_path)
    backup = tmp_path / "private" / "prediction_evidence_backup.json.gz"
    create_verified_backup(source, backup, created_at="2026-01-02T12:30:00Z")
    raw = bytearray(backup.read_bytes())
    raw[-1] ^= 1
    backup.write_bytes(bytes(raw))

    restored = PredictionStore(tmp_path / "restored" / "prediction_engine.sqlite3")
    with pytest.raises(ValueError, match="checksum mismatch"):
        restore_verified_backup(restored, backup)


def test_manifest_never_claims_formal_or_order_changes(tmp_path: Path) -> None:
    source = _populated_store(tmp_path)
    backup = tmp_path / "private" / "prediction_evidence_backup.json.gz"
    create_verified_backup(source, backup, created_at="2026-01-02T12:30:00Z")
    manifest_path = backup.with_suffix(backup.suffix + ".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["private_backup"] is True
    assert manifest["formal_v6_modified"] is False
    assert manifest["automatic_orders"] is False
