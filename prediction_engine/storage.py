"""Private SQLite storage for immutable forecasts and matured outcomes."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


SCHEMA_VERSION = 5
STABLE_MODEL_VERSION = "WUDE-PREDICT-ENGINE-V1-CHAMPION"
SHADOW_PROMOTION_SCOPE = "independent_shadow_engine_only"


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
                CREATE TABLE IF NOT EXISTS unit_learning_predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    unit_id TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    market TEXT NOT NULL,
                    asset_group TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    source_price REAL NOT NULL,
                    direction INTEGER NOT NULL,
                    strength REAL NOT NULL,
                    confidence REAL NOT NULL,
                    evidence_status TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    outcome_session_date TEXT,
                    outcome_price REAL,
                    realized_return_pct REAL,
                    direction_correct INTEGER,
                    created_at TEXT NOT NULL,
                    UNIQUE (unit_id, market, asset_group, session_date, symbol)
                );
                CREATE INDEX IF NOT EXISTS idx_unit_learning_pending
                    ON unit_learning_predictions (market, status, session_date);
                CREATE INDEX IF NOT EXISTS idx_unit_learning_metrics
                    ON unit_learning_predictions (unit_id, asset_group, status, session_date);
                CREATE TABLE IF NOT EXISTS unit_trust_control (
                    unit_id TEXT NOT NULL,
                    market TEXT NOT NULL,
                    asset_group TEXT NOT NULL,
                    active_multiplier REAL NOT NULL DEFAULT 1.0,
                    previous_multiplier REAL,
                    candidate_multiplier REAL,
                    status TEXT NOT NULL,
                    consecutive_wins INTEGER NOT NULL DEFAULT 0,
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_evaluated_through TEXT,
                    reason TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (unit_id, market, asset_group)
                );
                CREATE TABLE IF NOT EXISTS unit_trust_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    unit_id TEXT NOT NULL,
                    market TEXT NOT NULL,
                    asset_group TEXT NOT NULL,
                    previous_multiplier REAL NOT NULL,
                    new_multiplier REAL NOT NULL,
                    candidate_multiplier REAL NOT NULL,
                    event TEXT NOT NULL,
                    qualified INTEGER NOT NULL,
                    evaluated_through TEXT,
                    reason TEXT NOT NULL,
                    metrics_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (unit_id, market, asset_group, evaluated_through)
                );
                CREATE TABLE IF NOT EXISTS forward_outcome_cohorts (
                    market TEXT NOT NULL,
                    signal_session_date TEXT NOT NULL,
                    benchmark_symbol TEXT,
                    status TEXT NOT NULL DEFAULT 'waiting_entry',
                    entry_session_date TEXT,
                    benchmark_entry_open REAL,
                    benchmark_adjusted_entry REAL,
                    benchmark_split_factor REAL NOT NULL DEFAULT 1.0,
                    benchmark_last_close REAL,
                    observed_sessions INTEGER NOT NULL DEFAULT 0,
                    last_session_date TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (market, signal_session_date)
                );
                CREATE TABLE IF NOT EXISTS forward_outcome_candidates (
                    market TEXT NOT NULL,
                    signal_session_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    asset_group TEXT NOT NULL,
                    frozen_rank INTEGER,
                    frozen_qualified_rank INTEGER,
                    frozen_score REAL,
                    data_quality_pct REAL,
                    prior_json TEXT NOT NULL,
                    entry_open REAL,
                    adjusted_entry REAL,
                    split_factor REAL NOT NULL DEFAULT 1.0,
                    last_close REAL,
                    max_high REAL,
                    min_low REAL,
                    peak_session_no INTEGER,
                    data_complete INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY (market, signal_session_date, symbol),
                    FOREIGN KEY (market, signal_session_date)
                        REFERENCES forward_outcome_cohorts(market, signal_session_date)
                );
                CREATE TABLE IF NOT EXISTS forward_outcome_results (
                    market TEXT NOT NULL,
                    signal_session_date TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    horizon_code TEXT NOT NULL,
                    horizon_sessions INTEGER NOT NULL,
                    outcome_session_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    adjusted_entry REAL,
                    outcome_close REAL,
                    close_return_pct REAL,
                    max_return_pct REAL,
                    peak_session_no INTEGER,
                    max_drawdown_pct REAL,
                    net_close_return_pct REAL,
                    benchmark_return_pct REAL,
                    excess_return_pct REAL,
                    frozen_rank INTEGER,
                    frozen_qualified_rank INTEGER,
                    frozen_score REAL,
                    data_quality_pct REAL,
                    prior_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (market, signal_session_date, symbol, horizon_code)
                );
                CREATE INDEX IF NOT EXISTS idx_forward_results_training
                    ON forward_outcome_results(market,horizon_code,status,signal_session_date);
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

    @staticmethod
    def _forward_number(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @staticmethod
    def _forward_is_stock(row: dict[str, Any], market: str) -> bool:
        return (
            str(row.get("market") or "").upper() == market
            and "ETF" not in str(row.get("type") or "").upper()
            and bool(row.get("symbol"))
        )

    @staticmethod
    def _forward_benchmark(rows: list[dict[str, Any]], market: str) -> dict[str, Any] | None:
        target = "0050" if market == "TW" else "VOO"
        for row in rows:
            symbol = str(row.get("symbol") or "").upper().split(".", 1)[0]
            if str(row.get("market") or "").upper() == market and symbol == target:
                return row
        return None

    @staticmethod
    def _forward_splits(row: dict[str, Any] | None, session_date: str, entry_date: str | None) -> list[float]:
        if not row or not entry_date:
            return []
        ratios = []
        for event in row.get("official_stock_splits") or []:
            try:
                ratio = float(event.get("ratio") or 0)
            except (TypeError, ValueError, AttributeError):
                continue
            split_date = str(event.get("date") or "")
            if ratio > 0 and entry_date < split_date <= session_date and split_date == session_date:
                ratios.append(ratio)
        return ratios

    def record_forward_cohort(
        self,
        market: str,
        session_date: str,
        rows: list[dict[str, Any]],
        created_at: str,
    ) -> int:
        """Freeze every stock before any future session can become its answer."""
        benchmark = self._forward_benchmark(rows, market)
        benchmark_symbol = str((benchmark or {}).get("symbol") or ("0050.TW" if market == "TW" else "VOO"))
        candidates = [row for row in rows if self._forward_is_stock(row, market)]
        if not candidates:
            return 0
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO forward_outcome_cohorts("
                "market,signal_session_date,benchmark_symbol,created_at,updated_at) VALUES(?,?,?,?,?)",
                (market, session_date, benchmark_symbol, created_at, created_at),
            )
            values = []
            for fallback_rank, row in enumerate(candidates, 1):
                rank = row.get("overall_display_rank")
                qualified = row.get("overall_rank")
                score = row.get("overall_ranking_score", row.get("score"))
                quality = row.get("market_data_quality_score", row.get("overall_confidence"))
                prior = {
                    "action": row.get("action"),
                    "buy_candidate_status": row.get("buy_candidate_status"),
                    "buy_candidate_reasons": list(row.get("buy_candidate_reasons") or []),
                    "technical_score": row.get("technical_score"),
                    "volume_score": row.get("volume_score"),
                    "institution_score": row.get("institution_score"),
                    "entry_score": row.get("entry_score"),
                    "sector": row.get("sector"),
                    "market_regime": row.get("market_regime"),
                    "industry_lifecycle": row.get("_industry_lifecycle"),
                }
                values.append((
                    market, session_date, str(row["symbol"]),
                    str(row.get("name") or row["symbol"]), f"{market}_STOCK",
                    int(rank or fallback_rank), int(qualified) if qualified is not None else None,
                    float(score) if score is not None else None,
                    float(quality) if quality is not None else None,
                    json.dumps(prior, ensure_ascii=False, separators=(",", ":")),
                ))
            before = db.total_changes
            db.executemany(
                "INSERT OR IGNORE INTO forward_outcome_candidates("
                "market,signal_session_date,symbol,name,asset_group,frozen_rank,"
                "frozen_qualified_rank,frozen_score,data_quality_pct,prior_json) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                values,
            )
            return db.total_changes - before

    def advance_forward_outcomes(
        self,
        market: str,
        session_date: str,
        rows: list[dict[str, Any]],
        updated_at: str,
        *,
        horizons: dict[str, int],
        round_trip_cost_pct: float,
    ) -> int:
        """Advance old cohorts once; the signal session itself is never consumed."""
        row_map = {
            str(row.get("symbol")): row for row in rows
            if str(row.get("market") or "").upper() == market
        }
        benchmark_row = self._forward_benchmark(rows, market)
        settled = 0
        with self.connect() as db:
            cohorts = db.execute(
                "SELECT * FROM forward_outcome_cohorts WHERE market=? "
                "AND signal_session_date<? AND (last_session_date IS NULL OR last_session_date<?) "
                "AND observed_sessions<? ORDER BY signal_session_date",
                (market, session_date, session_date, max(horizons.values())),
            ).fetchall()
            for cohort in cohorts:
                session_no = int(cohort["observed_sessions"] or 0) + 1
                entry_date = cohort["entry_session_date"] or session_date
                benchmark_entry = cohort["benchmark_adjusted_entry"]
                benchmark_factor = float(cohort["benchmark_split_factor"] or 1.0)
                if session_no == 1:
                    benchmark_entry = self._forward_number((benchmark_row or {}).get("official_open_price"))
                for ratio in self._forward_splits(benchmark_row, session_date, entry_date):
                    benchmark_factor *= ratio
                    if benchmark_entry:
                        benchmark_entry /= ratio
                benchmark_close = self._forward_number((benchmark_row or {}).get("official_close_price"))
                benchmark_return = (
                    (benchmark_close / benchmark_entry - 1.0) * 100.0
                    if benchmark_close and benchmark_entry else None
                )
                candidates = db.execute(
                    "SELECT * FROM forward_outcome_candidates WHERE market=? AND signal_session_date=?",
                    (market, cohort["signal_session_date"]),
                ).fetchall()
                for candidate in candidates:
                    row = row_map.get(candidate["symbol"])
                    open_price = self._forward_number((row or {}).get("official_open_price"))
                    high = self._forward_number((row or {}).get("official_high_price"))
                    low = self._forward_number((row or {}).get("official_low_price"))
                    close = self._forward_number((row or {}).get("official_close_price"))
                    entry = candidate["adjusted_entry"]
                    factor = float(candidate["split_factor"] or 1.0)
                    max_high = candidate["max_high"]
                    min_low = candidate["min_low"]
                    peak_no = candidate["peak_session_no"]
                    complete = bool(candidate["data_complete"])
                    if session_no == 1:
                        entry = open_price
                        complete = complete and entry is not None
                    for ratio in self._forward_splits(row, session_date, entry_date):
                        factor *= ratio
                        if entry:
                            entry /= ratio
                        if max_high:
                            max_high /= ratio
                        if min_low:
                            min_low /= ratio
                    if not (high and low and close):
                        complete = False
                    else:
                        if max_high is None or high > float(max_high):
                            max_high = high
                            peak_no = session_no
                        min_low = low if min_low is None else min(float(min_low), low)
                    db.execute(
                        "UPDATE forward_outcome_candidates SET entry_open=COALESCE(entry_open,?),"
                        "adjusted_entry=?,split_factor=?,last_close=?,max_high=?,min_low=?,"
                        "peak_session_no=?,data_complete=? WHERE market=? AND signal_session_date=? AND symbol=?",
                        (
                            open_price if session_no == 1 else None, entry, factor, close,
                            max_high, min_low, peak_no, int(complete), market,
                            cohort["signal_session_date"], candidate["symbol"],
                        ),
                    )
                    for code, target in horizons.items():
                        if session_no != target:
                            continue
                        valid = bool(complete and entry and close and max_high and min_low)
                        close_return = (close / entry - 1.0) * 100.0 if valid else None
                        max_return = (float(max_high) / entry - 1.0) * 100.0 if valid else None
                        drawdown = (float(min_low) / entry - 1.0) * 100.0 if valid else None
                        excess = close_return - benchmark_return if valid and benchmark_return is not None else None
                        db.execute(
                            "INSERT OR IGNORE INTO forward_outcome_results("
                            "market,signal_session_date,symbol,name,horizon_code,horizon_sessions,"
                            "outcome_session_date,status,adjusted_entry,outcome_close,close_return_pct,"
                            "max_return_pct,peak_session_no,max_drawdown_pct,net_close_return_pct,"
                            "benchmark_return_pct,excess_return_pct,frozen_rank,frozen_qualified_rank,"
                            "frozen_score,data_quality_pct,prior_json,created_at) "
                            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                market, cohort["signal_session_date"], candidate["symbol"], candidate["name"],
                                code, target, session_date, "valid" if valid else "data_incomplete",
                                entry, close, close_return, max_return, peak_no, drawdown,
                                close_return - round_trip_cost_pct if close_return is not None else None,
                                benchmark_return, excess, candidate["frozen_rank"],
                                candidate["frozen_qualified_rank"], candidate["frozen_score"],
                                candidate["data_quality_pct"], candidate["prior_json"], updated_at,
                            ),
                        )
                        settled += 1
                db.execute(
                    "UPDATE forward_outcome_cohorts SET status=?,entry_session_date=?,"
                    "benchmark_entry_open=COALESCE(benchmark_entry_open,?),benchmark_adjusted_entry=?,"
                    "benchmark_split_factor=?,benchmark_last_close=?,observed_sessions=?,"
                    "last_session_date=?,updated_at=? WHERE market=? AND signal_session_date=?",
                    (
                        "matured" if session_no >= max(horizons.values()) else "tracking",
                        entry_date, benchmark_entry if session_no == 1 else None, benchmark_entry,
                        benchmark_factor, benchmark_close, session_no, session_date, updated_at,
                        market, cohort["signal_session_date"],
                    ),
                )
        return settled

    def forward_outcome_summary(self, horizons: dict[str, int]) -> dict[str, Any]:
        markets: dict[str, Any] = {}
        with self.connect() as db:
            for market in ("TW", "US"):
                horizon_summary = {}
                for code, target in horizons.items():
                    rows = db.execute(
                        "SELECT * FROM forward_outcome_results WHERE market=? AND horizon_code=? "
                        "ORDER BY signal_session_date,symbol",
                        (market, code),
                    ).fetchall()
                    valid = [row for row in rows if row["status"] == "valid"]
                    cohorts: dict[str, list[sqlite3.Row]] = {}
                    for row in valid:
                        cohorts.setdefault(str(row["signal_session_date"]), []).append(row)
                    caught10 = caught20 = actual10 = actual20 = 0
                    for values in cohorts.values():
                        winners = sorted(values, key=lambda row: (-float(row["max_return_pct"]), row["symbol"]))[:20]
                        actual10 += min(10, len(winners))
                        actual20 += len(winners)
                        caught10 += sum(int(row["frozen_rank"] or 10**9) <= 10 for row in winners[:10])
                        caught20 += sum(int(row["frozen_rank"] or 10**9) <= 20 for row in winners)
                    latest_date = max(cohorts, default="")
                    latest = sorted(
                        cohorts.get(latest_date, []),
                        key=lambda row: (-float(row["max_return_pct"]), row["symbol"]),
                    )[:10]
                    summary = {
                        "horizon_sessions": target,
                        "matured_cohorts": len(cohorts),
                        "valid_samples": len(valid),
                        "incomplete_samples": len(rows) - len(valid),
                        "top10_capture_rate_pct": round(caught10 / actual10 * 100, 4) if actual10 else None,
                        "top20_capture_rate_pct": round(caught20 / actual20 * 100, 4) if actual20 else None,
                        "latest_signal_session_date": latest_date or None,
                        "latest_winners": [{
                            "symbol": row["symbol"], "name": row["name"],
                            "max_return_pct": round(float(row["max_return_pct"]), 4),
                            "close_return_pct": round(float(row["close_return_pct"]), 4),
                            "net_close_return_pct": round(float(row["net_close_return_pct"]), 4),
                            "peak_session_no": row["peak_session_no"],
                            "max_drawdown_pct": round(float(row["max_drawdown_pct"]), 4),
                            "frozen_rank": row["frozen_rank"],
                            "captured_top10": int(row["frozen_rank"] or 10**9) <= 10,
                            "captured_top20": int(row["frozen_rank"] or 10**9) <= 20,
                            "benchmark_return_pct": (
                                round(float(row["benchmark_return_pct"]), 4)
                                if row["benchmark_return_pct"] is not None else None
                            ),
                            "excess_return_pct": (
                                round(float(row["excess_return_pct"]), 4)
                                if row["excess_return_pct"] is not None else None
                            ),
                            "industry_lifecycle": (json.loads(row["prior_json"]).get("industry_lifecycle") or {}),
                        } for row in latest],
                    }
                    if code == "UP_60D":
                        stages: dict[str, dict[str, float | int]] = {}
                        for row in valid:
                            lifecycle = json.loads(row["prior_json"]).get("industry_lifecycle") or {}
                            stage = str(lifecycle.get("stage") or "資料不足")
                            item = stages.setdefault(stage, {
                                "samples": 0, "positive_samples": 0, "return_sum_pct": 0.0,
                            })
                            realized = float(row["close_return_pct"])
                            item["samples"] += 1
                            item["positive_samples"] += int(realized > 0)
                            item["return_sum_pct"] += realized
                        stage_metrics = {}
                        for stage, item in stages.items():
                            samples = int(item["samples"])
                            stage_metrics[stage] = {
                                "samples": samples,
                                "positive_return_rate_pct": round(
                                    int(item["positive_samples"]) / samples * 100, 4
                                ) if samples else None,
                                "average_close_return_pct": round(
                                    float(item["return_sum_pct"]) / samples, 4
                                ) if samples else None,
                            }
                        completed = len(cohorts)
                        summary["industry_lifecycle_validation"] = {
                            "status": (
                                "ready_for_60d_shadow_comparison" if completed >= 60
                                else "preliminary_observation_only" if completed >= 20
                                else "collecting_without_conclusion"
                            ),
                            "completed_signal_cohorts": completed,
                            "preliminary_observation_sessions": 20,
                            "minimum_comparison_sessions": 60,
                            "can_compare_shadow_improvement": completed >= 60,
                            "can_modify_formal_v6": False,
                            "stages": stage_metrics,
                        }
                    horizon_summary[code] = summary
                counts = db.execute(
                    "SELECT COUNT(*),SUM(CASE WHEN status='waiting_entry' THEN 1 ELSE 0 END),"
                    "SUM(CASE WHEN status='tracking' THEN 1 ELSE 0 END) "
                    "FROM forward_outcome_cohorts WHERE market=?",
                    (market,),
                ).fetchone()
                markets[market] = {
                    "cohort_count": int(counts[0] or 0),
                    "waiting_entry_cohorts": int(counts[1] or 0),
                    "tracking_cohorts": int(counts[2] or 0),
                    "horizons": horizon_summary,
                }
        return {"markets": markets}

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
            # Stock up-horizon challengers learn from the stricter answer:
            # signal close frozen first, next official open as entry, then the
            # exact future horizon.  Before that answer matures there is no
            # substitute label. ETFs and direction-only horizons keep their
            # existing independent close-to-close ledger.
            if asset_group == f"{market}_STOCK" and horizon_code in {
                "UP_5D", "UP_45D", "UP_60D", "UP_126D"
            }:
                rows = db.execute(
                    "SELECT p.session_date,p.feature_json,r.close_return_pct realized_return_pct "
                    "FROM predictions p JOIN forward_outcome_results r "
                    "ON r.market=p.market AND r.signal_session_date=p.session_date "
                    "AND r.symbol=p.symbol AND r.horizon_code=p.horizon_code "
                    "WHERE p.market=? AND p.asset_group=? AND p.horizon_code=? "
                    "AND r.status='valid' ORDER BY p.session_date,p.symbol",
                    (market, asset_group, horizon_code),
                ).fetchall()
            else:
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
                "SELECT * FROM model_control WHERE market=? AND asset_group=? AND horizon_code=?",
                (market, asset_group, horizon_code),
            ).fetchone()
        if not row:
            return {
                "market": market,
                "asset_group": asset_group,
                "horizon_code": horizon_code,
                "active_model_version": STABLE_MODEL_VERSION,
                "previous_model_version": None,
                "candidate_model_version": None,
                "status": "stable_champion",
                "consecutive_wins": 0,
                "consecutive_failures": 0,
                "last_evaluated_through": None,
                "reason": "等待足夠樣本進行模型競賽",
                "metrics": {},
                "updated_at": None,
            }
        payload = dict(row)
        payload["metrics"] = json.loads(payload.pop("metrics_json"))
        return payload

    def selected_model(self, market: str, asset_group: str, horizon_code: str) -> dict[str, Any]:
        state = self.control_state(market, asset_group, horizon_code)
        version = state["active_model_version"]
        if version == STABLE_MODEL_VERSION:
            return {"model_version": version, "weights": None, "control": state}
        model = self.model_by_version(version)
        if not model:
            return {"model_version": STABLE_MODEL_VERSION, "weights": None, "control": {
                **state, "status": "safe_fallback", "reason": "主動模型檔不存在，改用穩定模型",
            }}
        return {"model_version": version, "weights": model["weights"], "control": state}

    def evaluate_candidate(
        self,
        payload: dict[str, Any],
        *,
        qualified: bool,
        reasons: list[str],
        required_consecutive_wins: int = 3,
        rollback_failures: int = 2,
        promotion_scope: str = "review_only",
    ) -> dict[str, Any]:
        """Apply controlled upgrades only inside the independent shadow engine.

        The default remains review-only.  A caller must explicitly provide the
        shadow-only scope, and only a challenger payload may then control this
        private engine.  Formal V6 does not use this database or this method.
        """
        if promotion_scope != SHADOW_PROMOTION_SCOPE:
            return self.record_candidate_review(
                payload, qualified=qualified, reasons=reasons
            )
        if payload.get("role") != "challenger":
            raise ValueError("only challenger models may enter shadow promotion")
        market = payload["market"]
        group = payload["asset_group"]
        horizon = payload["horizon_code"]
        state = self.control_state(market, group, horizon)
        trained_through = payload.get("trained_through")
        if state.get("last_evaluated_through") == trained_through:
            return state
        active = state["active_model_version"]
        active_before = active
        previous = state.get("previous_model_version")
        wins = int(state.get("consecutive_wins") or 0)
        failures = int(state.get("consecutive_failures") or 0)
        status = state.get("status") or "stable_champion"
        reason = "；".join(reasons) if reasons else "樣本外方向與誤差均優於穩定模型"
        if active == STABLE_MODEL_VERSION:
            wins = wins + 1 if qualified else 0
            failures = 0
            status = "challenger_confirming" if qualified else "stable_champion"
            if wins >= required_consecutive_wins:
                previous = active
                active = payload["model_version"]
                wins = 0
                status = "challenger_active"
                reason = "連續三個不同交易日樣本外勝出，下一交易日起接管獨立影子預判"
        elif qualified:
            wins += 1
            failures = 0
            status = "challenger_update_confirming"
            if wins >= required_consecutive_wins:
                previous = active
                active = payload["model_version"]
                wins = 0
                status = "challenger_active"
                reason = "新版連續三個不同交易日勝出，下一交易日起更新獨立影子預判"
        else:
            failures += 1
            wins = 0
            status = "active_model_warning"
            if failures >= rollback_failures:
                rollback_target = previous or STABLE_MODEL_VERSION
                if rollback_target != STABLE_MODEL_VERSION and not self.model_by_version(rollback_target):
                    rollback_target = STABLE_MODEL_VERSION
                previous = STABLE_MODEL_VERSION if rollback_target != STABLE_MODEL_VERSION else None
                active = rollback_target
                failures = 0
                status = (
                    "rolled_back_to_previous"
                    if active != STABLE_MODEL_VERSION
                    else "rolled_back_to_stable"
                )
                reason = "主動影子模型連續兩次未通過樣本外守門，下一交易日起退回上一個可用版本"
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO model_control VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    market, group, horizon, active, previous, payload["model_version"],
                    status, wins, failures, trained_through, reason,
                    json.dumps(payload.get("metrics") or {}, separators=(",", ":")),
                    payload["created_at"],
                ),
            )
            event = (
                "promoted" if active_before == STABLE_MODEL_VERSION and active != STABLE_MODEL_VERSION
                else "rolled_back" if status.startswith("rolled_back")
                else "challenger_updated" if active_before != active
                else "qualified_waiting" if qualified
                else "rejected_or_warning"
            )
            db.execute(
                """INSERT OR IGNORE INTO model_control_events(
                market,asset_group,horizon_code,previous_active_model_version,
                new_active_model_version,candidate_model_version,event,qualified,
                trained_through,reason,metrics_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    market, group, horizon, active_before, active,
                    payload["model_version"], event, int(qualified), trained_through,
                    reason, json.dumps(payload.get("metrics") or {}, separators=(",", ":")),
                    payload["created_at"],
                ),
            )
        return self.control_state(market, group, horizon)

    def record_candidate_review(
        self,
        payload: dict[str, Any],
        *,
        qualified: bool,
        reasons: list[str],
    ) -> dict[str, Any]:
        """Record a challenger result without allowing it to take control.

        Training and evaluation may run automatically.  Model selection is a
        separate human decision, so this method never changes active_model_version.
        """
        market = payload["market"]
        group = payload["asset_group"]
        horizon = payload["horizon_code"]
        state = self.control_state(market, group, horizon)
        trained_through = payload.get("trained_through")
        if state.get("last_evaluated_through") == trained_through:
            return state
        active = state["active_model_version"]
        wins = int(state.get("consecutive_wins") or 0) + 1 if qualified else 0
        failures = 0 if qualified else int(state.get("consecutive_failures") or 0) + 1
        status = "eligible_for_manual_review" if qualified else "challenger_not_qualified"
        reason = (
            "樣本外條件通過；此次呼叫未授權影子升級，僅列入審查"
            if qualified
            else "；".join(reasons) or "樣本外條件未通過"
        )
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO model_control VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    market, group, horizon, active, state.get("previous_model_version"),
                    payload["model_version"], status, wins, failures, trained_through,
                    reason, json.dumps(payload.get("metrics") or {}, separators=(",", ":")),
                    payload["created_at"],
                ),
            )
            db.execute(
                """INSERT OR IGNORE INTO model_control_events(
                market,asset_group,horizon_code,previous_active_model_version,
                new_active_model_version,candidate_model_version,event,qualified,
                trained_through,reason,metrics_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    market, group, horizon, active, active, payload["model_version"],
                    "qualified_waiting_manual_review" if qualified else "rejected",
                    int(qualified), trained_through, reason,
                    json.dumps(payload.get("metrics") or {}, separators=(",", ":")),
                    payload["created_at"],
                ),
            )
        return self.control_state(market, group, horizon)

    def all_control_states(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM model_control ORDER BY market,asset_group,horizon_code"
            ).fetchall()
        results = []
        for row in rows:
            payload = dict(row)
            payload["metrics"] = json.loads(payload.pop("metrics_json"))
            results.append(payload)
        return results

    def recent_control_events(self, limit: int = 24) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM model_control_events ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        results = []
        for row in rows:
            payload = dict(row)
            payload["qualified"] = bool(payload["qualified"])
            payload["metrics"] = json.loads(payload.pop("metrics_json"))
            results.append(payload)
        return results

    def create_portfolio(self, payload: dict[str, Any], positions: list[dict[str, Any]]) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO portfolios(
                market,horizon_code,session_date,capital,currency,horizon_sessions,status,created_at
                ) VALUES(?,?,?,?,?,?,'open',?)""",
                (
                    payload["market"], payload["horizon_code"], payload["session_date"],
                    payload["capital"], payload["currency"], payload["horizon_sessions"],
                    payload["created_at"],
                ),
            )
            if not cursor.rowcount:
                return False
            portfolio_id = int(cursor.lastrowid)
            db.executemany(
                "INSERT INTO portfolio_positions VALUES(?,?,?,?,?,NULL,NULL,'open')",
                [(
                    portfolio_id, row["symbol"], row["rank"], row["weight"],
                    row["entry_price"],
                ) for row in positions],
            )
            return True

    def settle_portfolios(self, market: str) -> int:
        with self.connect() as db:
            sessions = [row[0] for row in db.execute(
                "SELECT session_date FROM market_sessions WHERE market=? ORDER BY session_date",
                (market,),
            )]
            index = {day: idx for idx, day in enumerate(sessions)}
            portfolios = db.execute(
                "SELECT * FROM portfolios WHERE market=? AND status='open'", (market,)
            ).fetchall()
            settled = 0
            for portfolio in portfolios:
                start = index.get(portfolio["session_date"])
                target = None if start is None else start + int(portfolio["horizon_sessions"])
                if target is None or target >= len(sessions):
                    continue
                exit_day = sessions[target]
                positions = db.execute(
                    "SELECT * FROM portfolio_positions WHERE portfolio_id=?", (portfolio["id"],)
                ).fetchall()
                ending = 0.0
                completed = []
                for position in positions:
                    price = db.execute(
                        "SELECT close_price FROM prices WHERE market=? AND session_date=? AND symbol=?",
                        (market, exit_day, position["symbol"]),
                    ).fetchone()
                    if not price:
                        completed = []
                        break
                    allocation = float(portfolio["capital"]) * float(position["weight"])
                    value = allocation * float(price[0]) / float(position["entry_price"])
                    ending += value
                    completed.append((float(price[0]), value - allocation, portfolio["id"], position["symbol"]))
                if not completed or len(completed) != len(positions):
                    continue
                db.executemany(
                    "UPDATE portfolio_positions SET exit_price=?,pnl=?,status='closed' "
                    "WHERE portfolio_id=? AND symbol=?", completed,
                )
                result = (ending / float(portfolio["capital"]) - 1.0) * 100.0
                db.execute(
                    "UPDATE portfolios SET status='closed',exit_session_date=?,ending_value=?,return_pct=? WHERE id=?",
                    (exit_day, ending, result, portfolio["id"]),
                )
                settled += 1
            return settled

    def portfolio_summary(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT market,horizon_code,status,COUNT(*) count,"
                "AVG(return_pct) avg_return_pct,SUM(CASE WHEN return_pct>0 THEN 1 ELSE 0 END) wins "
                "FROM portfolios GROUP BY market,horizon_code,status ORDER BY market,horizon_code,status"
            ).fetchall()
        return [dict(row) for row in rows]

    def recent_portfolios(self, limit: int = 24) -> list[dict[str, Any]]:
        """Return compact portfolio details without exposing model feature rows."""
        with self.connect() as db:
            portfolios = db.execute(
                "SELECT * FROM portfolios ORDER BY session_date DESC,id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            results: list[dict[str, Any]] = []
            for portfolio in portfolios:
                positions = db.execute(
                    "SELECT symbol,rank,weight,entry_price,exit_price,pnl,status "
                    "FROM portfolio_positions WHERE portfolio_id=? ORDER BY rank",
                    (portfolio["id"],),
                ).fetchall()
                results.append({
                    "market": portfolio["market"],
                    "horizon_code": portfolio["horizon_code"],
                    "session_date": portfolio["session_date"],
                    "capital": portfolio["capital"],
                    "currency": portfolio["currency"],
                    "status": portfolio["status"],
                    "exit_session_date": portfolio["exit_session_date"],
                    "ending_value": portfolio["ending_value"],
                    "return_pct": portfolio["return_pct"],
                    "positions": [dict(position) for position in positions],
                })
        return results

    def record_source_usage(
        self,
        session_date: str,
        source: str,
        *,
        read_count: int = 0,
        network_requests: int = 0,
        cache_hits: int = 0,
        failure_count: int = 0,
    ) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO source_usage(
                session_date,source,read_count,network_requests,cache_hits,failure_count
                ) VALUES(?,?,?,?,?,?) ON CONFLICT(session_date,source) DO UPDATE SET
                read_count=excluded.read_count,
                network_requests=excluded.network_requests,
                cache_hits=excluded.cache_hits,
                failure_count=excluded.failure_count""",
                (
                    session_date, source, int(read_count), int(network_requests),
                    int(cache_hits), int(failure_count),
                ),
            )

    def maintain_capacity(self, *, keep_matured_sessions: int = 140) -> dict[str, Any]:
        """Bound storage while retaining pending labels and a rolling learning window."""
        with self.connect() as db:
            compacted = db.execute(
                "UPDATE predictions SET evidence_json='{}' "
                "WHERE status='matured' AND evidence_json<>'{}'"
            ).rowcount
            deleted = 0
            unit_deleted = 0
            for market in ("TW", "US"):
                sessions = [row[0] for row in db.execute(
                    "SELECT session_date FROM market_sessions WHERE market=? ORDER BY session_date DESC",
                    (market,),
                )]
                if len(sessions) <= keep_matured_sessions:
                    continue
                cutoff = sessions[keep_matured_sessions - 1]
                deleted += db.execute(
                    "DELETE FROM predictions WHERE market=? AND status='matured' AND session_date<?",
                    (market, cutoff),
                ).rowcount
                unit_deleted += db.execute(
                    "DELETE FROM unit_learning_predictions "
                    "WHERE market=? AND status='matured' AND session_date<?",
                    (market, cutoff),
                ).rowcount
                db.execute(
                    "DELETE FROM prices WHERE market=? AND session_date<? AND session_date NOT IN "
                    "(SELECT DISTINCT session_date FROM predictions WHERE market=?)",
                    (market, cutoff, market),
                )
        with self.connect() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        size = self.path.stat().st_size if self.path.exists() else 0
        vacuumed = False
        if self.max_bytes and size / self.max_bytes >= 0.8 and (deleted or unit_deleted):
            with self.connect() as db:
                db.execute("VACUUM")
            vacuumed = True
        return {
            "matured_evidence_compacted": int(compacted),
            "old_matured_predictions_pruned": int(deleted),
            "old_matured_unit_rows_pruned": int(unit_deleted),
            "rolling_matured_sessions_retained": int(keep_matured_sessions),
            "pending_predictions_never_pruned": True,
            "vacuumed": vacuumed,
        }

    def health(self) -> dict[str, Any]:
        size = self.path.stat().st_size if self.path.exists() else 0
        with self.connect() as db:
            tables = {}
            for name in (
                "market_sessions", "prices", "predictions", "model_versions",
                "model_control", "model_control_events", "unit_learning_predictions",
                "unit_trust_control", "unit_trust_events", "portfolios",
                "forward_outcome_cohorts", "forward_outcome_candidates",
                "forward_outcome_results",
            ):
                tables[name] = int(db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            mature = int(db.execute(
                "SELECT COUNT(*) FROM predictions WHERE status='matured'"
            ).fetchone()[0])
        ratio = size / self.max_bytes if self.max_bytes else 0.0
        return {
            "schema_version": SCHEMA_VERSION,
            "database_bytes": size,
            "configured_max_bytes": self.max_bytes,
            "capacity_pct": round(ratio * 100, 2),
            "capacity_status": "blocked" if ratio >= 1 else "warning" if ratio >= 0.8 else "ok",
            "tables": tables,
            "matured_predictions": mature,
            "public_database_exposed": False,
        }
