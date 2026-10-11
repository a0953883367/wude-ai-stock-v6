# 完整觀察清單與逐檔完整性檢查

`watchlist.py` 是唯一 canonical 來源。開頭的 `WATCHLIST = [...]` 不是完整清單；
後續每個 `WATCHLIST.extend(...)` 都必須計入。`watchlist_manifest.json` 是由實際
`load_watchlist()` 結果生成的完整、可機器讀取版本，不需要讀取者自行解讀 Python。

## 產生與驗證

在可信的 repository checkout，以 Python 3.12 執行，無需行情或其他第三方套件：

```sh
python watchlist_manifest.py --write
python watchlist_manifest.py --check
python -m unittest discover -s tests -p test_watchlist_manifest.py
```

變更 watchlist 時，將重新產生的 JSON 一起提交。CI 會檢查生成物是否完整且最新，
JSON 單獨變更也會觸發檢查。未知的 extend 寫法、沒有納入來源分組的標的、或同一
market/symbol 具有衝突資料時，生成會失敗，不能默默跳過。

來源欄位含 watchlist 原始 bytes 的 SHA-256 與 Git blob SHA-1，總數與分組數由程式
計算；universe_sha256 是 entries 的排序鍵 compact UTF-8 JSON SHA-256。沒有會
隨每次生成改變的時間戳，故同一份來源可重現同一產物。來源 hash 是內容識別，
不是簽章、行情新鮮度或已發布 commit 的證明。

## 範圍與不變條件

- entries 保留完整原始欄位與順序；識別鍵為 market + symbol，保留 `.TW`／`.TWO`。
- source_groups 列出完整基礎清單及每一個 extend 分組，不使用固定舊總數。
- counts.per_symbol_required 表示必須逐檔交代的數量，不代表可排名、可買進或資料合格。
- HNHPF 的 reference_only、primary_symbol 與 OTC 資訊原樣保留，仍須在參考區交代。
- manifest 沒有行情、評分、買區、訊號；不修改 V6、正式排名、下單或授權判斷。
- 使用者 prompt 額外清單不是 canonical。下游若需要聯集，應保存另外的明確來源與
  驗證記錄，再按 market/symbol 去重；不能悄悄把 prompt 額外標的寫回本檔。

## 報告完整性 gate

使用 structured coverage sidecar，並執行：

```sh
python watchlist_manifest.py --check --coverage canonical-report-coverage.json
```

sidecar 格式：

```json
{
  "universe_sha256": "從本次完整 manifest 複製",
  "source_sha256": "從 manifest.source.sha256 複製",
  "rows": [
    {"market": "TW", "symbol": "2327.TW", "status": "analyzed"},
    {"market": "US", "symbol": "HNHPF", "status": "reference_only"}
  ]
}
```

以上只展示格式，省略其他標的，因此不能通過檢查。每個 canonical entry 都必須剛好
一次：一般標的 status 為 analyzed 或 unavailable；unavailable 必須提供非空 reason。
參考標的 status 只能是 reference_only。漏檔、重複、額外標的、錯誤角色或過期來源
hash 均失敗，不能以總數相同替代逐檔集合核對。

這個 gate 只驗證 structured sidecar 的身份與交代狀態，不會驗證自然語言報告內容、
來源授權、行情品質或排名／買進資格。消費端必須從實際最終報告產生 sidecar，並在
輸出前執行 gate；不可先把 expected 清單填入當成已分析。

此 repository 工具不會變更或控制外部 ChatGPT 排程。合併或發布這些檔案本身，
不能證明排程已讀取 manifest 或已接上 gate。端到端完成仍需有權限的消費端整合，
以及下一份實際報告逐檔完整性的驗證。

## 有來源證明的任務補充清單與聯集

`report_universe.py` 接收分開保存的 task_report_supplement，輸出 report_union；
不修改 canonical 或任何排程。真實任務 ID、prompt readback 等來源資料應留在私人
工作資料中，公開 repository 只包含通用工具與合成測試，不包含使用者 prompt。

supplement 必須包含 schema_version 1、scope task_report_supplement、entries、
entry_count、entries_sha256，以及 source 的 verification stored_prompt_readback、
含時區的 readback_at、tasks[{task_id, prompt_sha256}]。entries 使用相同的
key／security／coverage_role 結構；市場與型別必須來自明確來源，不能從建議推測。
readback 僅能證明核對過的特定任務，不得把某一午報 prompt 的 hash 套用到早晚報。

```sh
python report_universe.py --write --supplement /private/task-supplement.json --output /private/report-universe.json
python report_universe.py --check --supplement /private/task-supplement.json --output /private/report-universe.json
python -m unittest discover -s tests -p test_report_universe.py
```

聯集保持 canonical 先、補充清單後的順序，按 market/symbol 去重；重疊時 canonical
欄位優先，尤其不能把 reference_only 升為一般標的。每檔 origins 區分
canonical_watchlist、task_supplement 或兩者。counts 動態計算 canonical_entries、
supplement_entries、overlapping_entries、unique_entries、per_symbol_required、
reference_only 與 by_market，不使用永久固定總數。

inputs 的 canonical_manifest_sha256、task_supplement_sha256 分別識別兩份完整输入
JSON；inputs_sha256 識別 inputs 物件，universe_sha256 識別 entries。全部使用
sorted-key、compact、ensure_ascii=False 的 UTF-8 JSON，不附換行。source 保留
canonical watchlist 來源；supplement_source 則明確保存任務補充來源，不混在一起。
同一組標的若 prompt 來源改變，inputs_sha256 也會改變。

下游在可信來源上使用 validate_report_universe(union, canonical, supplement)
核對完整來源、hash、計數、順序與 origins，再從最終報告產生 coverage sidecar。
聯集 sidecar 除了原有 source_sha256／universe_sha256，還必須附 inputs_sha256；
使用上面 --check 指令加 --coverage /private/report-coverage.json 即可檢查全聯集。
此 SHA 核對提供一致性檢查，不會代替消費端重新核對任務來源、授權與行情品質。
