from memory_compaction import compact_messages, estimate_tokens


def test_compaction_preserves_decisions_constraints_pending_and_recent():
    messages = [
        {"role": "user", "content": "決定採用五層資料治理", "namespace": "wt_fasteners"},
        {"role": "user", "content": "不要自動上架商品", "namespace": "wt_fasteners"},
        {"role": "assistant", "content": "尚未完成正式商店連線", "namespace": "wt_fasteners"},
        {"role": "user", "content": "重複事實", "namespace": "wt_fasteners"},
        {"role": "user", "content": "重複事實", "namespace": "wt_fasteners"},
        {"role": "user", "content": "最新問題", "namespace": "wt_fasteners"},
    ]
    result = compact_messages(messages, namespace="wt_fasteners", keep_recent=1)
    assert result["summary"]["decisions"] == ["決定採用五層資料治理"]
    assert result["summary"]["constraints"] == ["不要自動上架商品"]
    assert result["summary"]["pending"] == ["尚未完成正式商店連線"]
    assert result["summary"]["facts"] == ["重複事實"]
    assert result["recent"][0]["content"] == "最新問題"
    assert result["raw_history_stored"] is False


def test_compaction_is_domain_isolated():
    messages = [
        {"role": "user", "content": "WT 資料", "namespace": "wt_fasteners"},
        {"role": "user", "content": "股票機密", "namespace": "stock_shadow"},
    ]
    result = compact_messages(messages, namespace="wt_fasteners", keep_recent=2)
    assert result["source_message_count"] == 1
    assert "股票機密" not in str(result)
    assert result["cross_domain_memory"] is False


def test_token_estimate_is_deterministic():
    assert estimate_tokens("abc 中文") == estimate_tokens("abc 中文")
    assert estimate_tokens("abc 中文") > 0
