"""Private SQLite storage for immutable forecasts and matured outcomes."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 3
STABLE_MODEL_VERSION = "WUDE-PREDICT-ENGINE-V1-CHAMPION"


class PredictionStore:
    def __init__(self, path: Path, *, max_bytes: int = 500 * 1024 * 1024):
        self.path = Path(path)
        self.max_bytes = int(max_bytes)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=20000")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                PRAGMA journal_size_limit=8388608;
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS market_sessions (
                    market TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    PRIMARY KEY (market, session_date)
                );
                CREATE TABLE IF NOT EXISTS prices (
                    market TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    asset_group TEXT NOT NULL,
                    close_price REAL NOT NULL,
                    source TEXT NOT NULL,
                    PRIMARY KEY (market, session_date, symbol)
                );
                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market TEXT NOT NULL,
                    asset_group TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    horizon_code TEXT NOT NULL,
                    horizon_sessions INTEGER NOT NULL,
                    target_side TEXT NOT NULL,
                    model_version TEXT NOT NULL,
                    source_price REAL NOT NULL,
                    probability_pct REAL NOT NULL,
                    expected_return_pct REAL NOT NULL,
                    buyability_score REAL NOT NULL,
                    downside_risk_pct REAL NOT NULL,
                    data_quality_pct REAL NOT NULL,
                    feature_json TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    outcome_session_date TEXT,
                    outcome_price REAL,
                    realized_return_pct REAL,
                    direction_correct INTEGER,
                    created_at TEXT NOT NULL,
                    UNIQUE (market, session_date, symbol, horizon_code, model_version)
                );
                CREATE INDEX IF NOT EXISTS idx_predictions_pending
                    ON predictions (market, status, horizon_sessions, session_date);
                CREATE INDEX IF NOT EXISTS idx_predictions_training
                    ON predictions (market, asset_group, horizon_code, status);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_predictions_immutable_answer
                    ON predictions (market, session_date, symbol, horizon_code);
                CREATE TABLE IF NOT EXISTS model_versions (
                    model_version TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    asset_group TEXT NOT NULL,
                    horizon_code TEXT NOT NULL,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    trained_through TEXT,
                    sample_count INTEGER NOT NULL,
                    session_count INTEGER NOT NULL,
                    weights_json TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS portfolios (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market TEXT NOT NULL,
                    horizon_code TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    capital REAL NOT NULL,
                    currency TEXT NOT NULL,
                    horizon_sessions INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    exit_session_date TEXT,
                    ending_value REAL,
                    return_pct REAL,
                    created_at TEXT NOT NULL,
                    UNIQUE (market, horizon_code, session_date)
                );
                CREATE TABLE IF NOT EXISTS portfolio_positions (
                    portfolio_id INTEGER NOT NULL REFERENCES portfolios(id),
                    symbol TEXT NOT NULL,
                    rank INTEGER NOT NULL,
                    weight REAL NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL,
                    pnl REAL,
                    status TEXT NOT NULL DEFAULT 'open',
                    PRIMARY KEY (portfolio_id, symbol)
                );
                CREATE TABLE IF NOT EXISTS source_usage (
                    session_date TEXT NOT NULL,
                    source TEXT NOT NULL,
                    read_count INTEGER NOT NULL DEFAULT 0,
                    network_requests INTEGER NOT NULL DEFAULT 0,
                    cache_hits INTEGER NOT NULL DEFAULT 0,
                    failure_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (session_date, source)
                );
                CREATE TABLE IF NOT EXISTS model_control (
                    market TEXT NOT NULL,
                    asset_group TEXT NOT NULL,
                    horizon_code TEXT NOT NULL,
                    active_model_version TEXT NOT NULL,
                    previous_model_version TEXT,
                    candidate_model_version TEXT,
                    status TEXT NOT NULL,
                    consecutive_wins INTEGER NOT NULL DEFAULT 0,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_evaluated_through TEXT,
                    reason TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (market, asset_group, horizon_code)
                );
                CREATE TABLE IF NOT EXISTS model_control_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market TEXT NOT NULL,
                    asset_group TEXT NOT NULL,
                    horizon_code TEXT NOT NULL,
                    previous_active_model_version TEXT NOT NULL,
                    new_active_model_version TEXT NOT NULL,
                    candidate_model_version TEXT NOT NULL,
                    event TEXT NOT NULL,
                    qualified INTEGER NOT NULL,
                    trained_through TEXT,
                    reason TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (market, asset_group, horizon_code, trained_through)
                );
                """
            )
            db.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES('schema_version',?)",
                (str(SCHEMA_VERSION),),
            )

    def record_session(self, market: str, session_date: str, captured_at: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO market_sessions VALUES(?,?,?)",
                (market, session_date, captured_at),
            )

    def session_count(self) -> int:
        with self.connect() as db:
            return int(db.execute("SELECT COUNT(*) FROM market_sessions").fetchone()[0])

    def record_prices(self, rows: list[dict[str, Any]], session_date: str) -> None:
        values = []
        for row in rows:
            price = float(row.get("official_close_price") or row.get("price") or 0)
            if price <= 0:
                continue
            market = str(row.get("market") or "").upper()
            asset = "ETF" if "ETF" in str(row.get("type") or "").upper() else "STOCK"
            values.append((
                market, session_date, str(row.get("symbol")),
                str(row.get("name") or row.get("symbol")), f"{market}_{asset}",
                price, str(row.get("tw_price_source") or row.get("price_source") or "completed_close"),
            ))
        with self.connect() as db:
            db.executemany(
                "INSERT OR IGNORE INTO prices VALUES(?,?,?,?,?,?,?)", values
            )

    def insert_predictions(self, rows: list[dict[str, Any]]) -> int:
        if self.path.exists() and self.path.stat().st_size >= self.max_bytes:
            raise RuntimeError("prediction database reached configured hard size limit")
        values = [(
            row["market"], row["asset_group"], row["session_date"], row["symbol"],
            row["name"], row["horizon_code"], row["horizon_sessions"],
            row["target_side"], row["model_version"], row["source_price"],
            row["probability_pct"], row["expected_return_pct"],
            row["buyability_score"], row["downside_risk_pct"],
            row["data_quality_pct"], json.dumps(row["features"], separators=(",", ":")),
            json.dumps(row["evidence"], ensure_ascii=False, separators=(",", ":")),
            row.get("status", "pending"), row["created_at"],
        ) for row in rows]
        with self.connect() as db:
            before = db.total_changes
            db.executemany(
                """INSERT OR IGNORE INTO predictions(
                market,asset_group,session_date,symbol,name,horizon_code,
                horizon_sessions,target_side,model_version,source_price,
                probability_pct,expected_return_pct,buyability_score,
                downside_risk_pct,data_quality_pct,feature_json,evidence_json,
                status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                values,
            )
            return db.total_changes - before

    def settle_matured(self, market: str) -> int:
        with self.connect() as db:
            sessions = [row[0] for row in db.execute(
                "SELECT session_date FROM market_sessions WHERE market=? ORDER BY session_date",
                (market,),
            )]
            index = {day: idx for idx, day in enumerate(sessions)}
            pending = db.execute(
                "SELECT id,session_date,symbol,horizon_sessions,target_side,source_price "
                "FROM predictions WHERE market=? AND status='pending'",
                (market,),
            ).fetchall()
            updates = []
            for row in pending:
                source_idx = index.get(row["session_date"])
                if source_idx is None:
                    continue
                target_idx = source_idx + int(row["horizon_sessions"])
                if target_idx >= len(sessions):
                    continue
                target_day = sessions[target_idx]
                price_row = db.execute(
                    "SELECT close_price FROM prices WHERE market=? AND session_date=? AND symbol=?",
                    (market, target_day, row["symbol"]),
                ).fetchone()
                if not price_row or float(price_row[0]) <= 0:
                    continue
                outcome = float(price_row[0])
                realized = (outcome / float(row["source_price"]) - 1.0) * 100.0
                correct = realized > 0 if row["target_side"] == "UP" else realized < 0
                updates.append((target_day, outcome, realized, int(correct), row["id"]))
            db.executemany(
                "UPDATE predictions SET status='matured',outcome_session_date=?,"
                "outcome_price=?,realized_return_pct=?,direction_correct=? WHERE id=?",
                updates,
            )
            return len(updates)

    def training_rows(self, market: str, asset_group: str, horizon_code: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT session_date,feature_json,realized_return_pct FROM predictions "
                "WHERE market=? AND asset_group=? AND horizon_code=? AND status='matured' "
                "ORDER BY session_date,symbol",
                (market, asset_group, horizon_code),
            ).fetchall()
        return [{
            "session_date": row["session_date"],
            "features": json.loads(row["feature_json"]),
            "realized_return_pct": float(row["realized_return_pct"]),
        } for row in rows]

    def save_model(self, payload: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO model_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    payload["model_version"], payload["market"], payload["asset_group"],
                    payload["horizon_code"], payload["role"], payload["status"],
                    payload.get("trained_through"), payload["sample_count"],
                    payload["session_count"], json.dumps(payload["weights"], separators=(",", ":")),
                    json.dumps(payload["metrics"], separators=(",", ":")), payload["created_at"],
                ),
            )

    def latest_challenger(self, market: str, asset_group: str, horizon_code: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM model_versions WHERE market=? AND asset_group=? "
                "AND horizon_code=? AND role='challenger' ORDER BY created_at DESC LIMIT 1",
                (market, asset_group, horizon_code),
            ).fetchone()
        if not row:
            return None
        return {
            **dict(row),
            "weights": json.loads(row["weights_json"]),
            "metrics": json.loads(row["metrics_json"]),
        }

    def latest_predictions(self) -> list[dict[str, Any]]:
        """Return only the newest immutable session for every market/horizon."""
        with self.connect() as db:
            rows = db.execute(
                """SELECT p.* FROM predictions p JOIN (
                    SELECT market,horizon_code,MAX(session_date) session_date
                    FROM predictions GROUP BY market,horizon_code
                ) latest ON latest.market=p.market
                    AND latest.horizon_code=p.horizon_code
                    AND latest.session_date=p.session_date
                ORDER BY p.market,p.asset_group,p.horizon_code,p.symbol""",
            ).fetchall()
        return [{
            **dict(row),
            "features": json.loads(row["feature_json"]),
            "evidence": json.loads(row["evidence_json"]),
        } for row in rows]

    def model_by_version(self, model_version: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM model_versions WHERE model_version=?", (model_version,)
            ).fetchone()
        if not row:
            return None
        return {
            **dict(row),
            "weights": json.loads(row["weights_json"]),
            "metrics": json.loads(row["metrics_json"]),
        }

    def control_state(self, market: str, asset_group: str, horizon_code: str) -> dict[str, Any]:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM model_control WHERE market=? AND asset_group=? AND hor