"""Run the independent prediction engine from an existing report directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from prediction_engine import run_prediction_engine


def main() -> int:
    parser = argparse.ArgumentParser(description="Build isolated multi-horizon forecasts")
    parser.add_argument("--reports-dir", default="reports")
    parser.add_argument("--db-path")
    parser.add_argument("--period")
    parser.add_argument("--intraday", action="store_true")
    args = parser.parse_args()
    reports_dir = Path(args.reports_dir)
    analysis = json.loads((reports_dir / "all_analysis.json").read_text(encoding="utf-8"))
    rows = analysis.get("data") if isinstance(analysis.get("data"), list) else []
    result = run_prediction_engine(
        reports_dir,
        rows,
        period=str(args.period or analysis.get("period") or "test"),
        updated_at=str(analysis.get("updated_at") or ""),
        intraday=bool(args.intraday or analysis.get("run_mode") == "intraday_refresh"),
        db_path=Path(args.db_path) if args.db_path else None,
    )
    print(json.dumps(result["run_summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
