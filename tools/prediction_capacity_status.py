#!/usr/bin/env python3
"""Read-only runner disk/cache metadata; never open or initialize the private DB."""
import json
from pathlib import Path
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prediction_engine.storage import configured_max_bytes


def capacity_status(root: Path) -> dict:
    disk = shutil.disk_usage(root)
    private = root / '.prediction_engine'
    def size(name):
        path = private / name
        return path.stat().st_size if path.is_file() else None
    return {
        'configured_max_bytes': configured_max_bytes(),
        'filesystem_free_bytes': disk.free,
        'filesystem_total_bytes': disk.total,
        'cached_database_bytes': size('prediction_engine.sqlite3'),
        'cached_backup_bytes': size('prediction_evidence_backup.json.gz'),
        'provider_cache_quota_verified': False,
        'private_content_read': False,
    }


if __name__ == '__main__':
    print(json.dumps(capacity_status(Path.cwd()), sort_keys=True))
