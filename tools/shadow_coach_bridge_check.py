#!/usr/bin/env python3
"""Offline acceptance of saved coaching; never writes production reports or calls APIs."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from market_calendar import OfficialMarketCalendar
from prediction_history_storage import read_prediction_history
from shadow_coach_bridge import build_reviewed_bridge, digest
from shadow_coach_definitions import REVIEWED_REPORT_SHA256, REVIEWED_SOURCE_COMMIT


def check(reports_dir, generated_at):
    paths = [reports_dir / name for name in ("weekly_shadow_coach.json", "prediction_history.json", "prediction_history.json.gz", "official_market_calendar.json")]
    def hashes():
        return {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.exists()}
    before = hashes()
    latest = reports_dir / "weekly_shadow_coach.json"
    report = json.loads(latest.read_text()) if latest.exists() else {}
    history = read_prediction_history(reports_dir / "prediction_history.json")
    calendar = OfficialMarketCalendar(reports_dir / "official_market_calendar.json", auto_refresh=False, allow_network=False)
    state = build_reviewed_bridge(report, {}, history, calendar, generated_at)
    if hashes() != before:
        raise ValueError("input_files_changed_during_read_only_check")
    return {"kind": "offline_saved_proposal_ingestion_acceptance", "generated_at": generated_at,
            "live_market_execution_claimed": False, "effectiveness": "unproven",
            "source_commit": REVIEWED_SOURCE_COMMIT, "reviewed_report_sha256": REVIEWED_REPORT_SHA256,
            "latest_report_sha256": digest(report), "input_file_sha256": before,
            "inputs_unchanged": True, "historical_snapshots_excluded": len(history.get("snapshots", [])),
            "source_status": state["source_status"], "reviewed_source_status": state["reviewed_source_status"],
            "experiments": [{"experiment_id": key, "proposal": e["proposal"], "contract": e["contract"],
                             "registered_at": e["registered_at"], "status": e["status"],
                             "registered_rows": e["registered_rows"], "completed_rows": e["completed_rows"],
                             "execution_verified": e["execution_verified"], "outcome_verified": e["outcome_verified"]}
                            for key, e in state["experiments"].items()]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reports-dir", type=Path, default=Path("reports"))
    parser.add_argument("--at", default="")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.output and args.output.resolve().is_relative_to(args.reports_dir.resolve()):
        parser.error("offline evidence output must be outside production reports")
    result = check(args.reports_dir, args.at or datetime.now(timezone.utc).isoformat())
    encoded = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(encoded)
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
