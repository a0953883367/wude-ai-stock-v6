#!/usr/bin/env python3
"""用假資料驗證本人版與朋友版發布隔離；不連網站、不讀祕密、不改正式資料。"""

from __future__ import annotations

import json
from typing import Any

from tools.publish_friend_data import sanitize, sanitize_accuracy
from tools.publish_owner_data import CHUNK_PROTOCOL, build_chunked_bodies


PRIVATE_MARKER = "OWNER_ONLY_MARKER_MUST_NOT_LEAK"
FORBIDDEN_FRIEND_KEYS = {
    "account",
    "api_key",
    "broker_token",
    "certificate",
    "inventory",
    "private_holding",
    "formal_weights",
    "model_formula",
}


def inspect_publication_isolation() -> dict[str, Any]:
    errors: list[str] = []
    source = {
        "symbol": "2330.TW",
        "name": "測試公司",
        "market": "TW",
        "type": "個股",
        "industry": "半導體",
        "score": 75,
        "price": 1000,
        "overall_display_rank": 1,
        "overall_rank": 1,
        "overall_eligible": True,
        "action": "觀察",
        "account": PRIVATE_MARKER,
        "api_key": PRIVATE_MARKER,
        "broker_token": PRIVATE_MARKER,
        "certificate": PRIVATE_MARKER,
        "inventory": [PRIVATE_MARKER],
        "formal_weights": {"secret": PRIVATE_MARKER},
        "model_formula": PRIVATE_MARKER,
    }
    friend = {
        "stocks": [sanitize(source)],
        "accuracy": sanitize_accuracy({"private": PRIVATE_MARKER}),
    }
    friend_text = json.dumps(friend, ensure_ascii=False, sort_keys=True)
    friend_keys = set(friend["stocks"][0])
    leaked_keys = sorted(FORBIDDEN_FRIEND_KEYS & friend_keys)
    if leaked_keys:
        errors.append("朋友版含有禁止欄位：" + ", ".join(leaked_keys))
    if PRIVATE_MARKER in friend_text:
        errors.append("朋友版內容洩漏本人版標記")
    if friend["accuracy"] != {}:
        errors.append("朋友版未清除驗證與訓練統計")

    owner_payload = json.dumps(
        {
            "data": [source],
            "private_holding": {"account": PRIVATE_MARKER},
            "rotation": None,
            "integrity": {"fake_test": True},
            "updated_at": "fake",
            "period": "test",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    chunks, commit = build_chunked_bodies(
        owner_payload, generation=1, chunk_size=1
    )
    owner_text = json.dumps(
        {"chunks": chunks, "commit": commit},
        ensure_ascii=False,
        sort_keys=True,
    )
    if PRIVATE_MARKER not in owner_text:
        errors.append("本人版完整資料在分塊流程中遺失")
    if commit.get("protocol") != CHUNK_PROTOCOL or "data" in commit:
        errors.append("本人版提交封包不符合分塊協定")
    if len(chunks) != 1 or chunks[0].get("data") != [source]:
        errors.append("本人版分塊資料不完整")

    return {
        "status": "passed" if not errors else "failed",
        "scope": "只使用記憶體內假資料驗證發布隔離，不連外部網站、不讀密鑰、不修改正式資料",
        "checks": {
            "friend_private_marker_absent": PRIVATE_MARKER not in friend_text,
            "friend_private_keys_absent": not leaked_keys,
            "friend_accuracy_cleared": friend["accuracy"] == {},
            "owner_complete_snapshot_preserved": PRIVATE_MARKER in owner_text,
            "owner_chunk_protocol_valid": commit.get("protocol") == CHUNK_PROTOCOL and "data" not in commit,
        },
        "errors": errors,
    }


def main() -> int:
    result = inspect_publication_isolation()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
