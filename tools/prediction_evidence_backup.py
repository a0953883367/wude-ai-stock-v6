#!/usr/bin/env python3
"""Create, verify, or restore the private shadow-learning evidence backup."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prediction_engine.evidence_backup import (  # noqa: E402
    create_verified_backup,
    restore_verified_backup,
)
from prediction_engine.storage import PredictionStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("create", "restore"))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--created-at")
    args = parser.parse_args()
    store = PredictionStore(args.database)
    if args.mode == "create":
        result = create_verified_backup(
            store,
            args.backup,
            manifest_path=args.manifest,
            created_at=args.created_at,
        )
    else:
        result = restore_verified_backup(
            store, args.backup, manifest_path=args.manifest
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
