# 完整研究交付

既有產報流程在發布 all_analysis.json 後產生 reports/research_delivery.json。首次部署後須等原有產報流程自然執行；程式部署不表示原生 ChatGPT 任務已讀取成功。

公開入口為 https://raw.githubusercontent.com/a0953883367/wude-ai-stock-v6/main/reports/research_delivery.json ，GitHub Contents入口為 https://api.github.com/repos/a0953883367/wude-ai-stock-v6/contents/reports/research_delivery.json 。這些路徑在首次自然產報前尚不存在。latest.json仍維持原有大檔格式，沒有改接新入口；README提供入口不會自動修改原生任務。

原生任務最短讀取指令：

> 從 a0953883367/wude-ai-stock-v6 的 reports/research_delivery.json 讀取完整研究。先固定 main 的commit，依索引讀齊全部judgment與prediction分片並核對批次、SHA256、身份及10期間；依逐檔判斷呈現新進場、若已持有、正反證據及季度／長期缺項。空內容或缺片須明列未完成，不以TOP或補充名單替代，不宣稱買進資格。

以上僅提供指令，沒有套用到任何原生任務；切換入口與實際完整讀取仍待另行驗收。

一般讀取工具以此小索引為入口，避免下載約14 MB的 all_analysis。索引不更改原分析、模型或排程，不新增模型呼叫。judgment分片只投影已公開的身份、名稱與完整判斷；10期間證據重用 prediction_engine.json 宣告的全部 predictions 分片，不重算、不使用TOP rankings代替。現有380身份／3800期間／52個預測分片不是新買進建議，也不是永久硬編碼的觀察池上限。

讀取端必須：

1. 先解析 main 為一個確切commit，之後索引與所有檔案都使用該ref。核對索引 status=complete、updated_at 是所需批次。
2. 依 judgment_files 與 prediction_files 讀取所有宣告檔案，檢查UTF-8原始位元組長度、SHA256與批次。Contents API的空content、只回傳SHA、截斷或任何缺片均不是成功。
3. 依 identities 中的 market＋symbol 與 horizon_code 對應 prediction_files 的 file_id，再以其 path 讀取完整 predictions，逐檔核對10期間；不能將 target_side 當預測方向，應使用帶正負號的 expected_return_pct。原始 probability_pct 不代表校準勝率。
4. 驗證身份無重複、無缺漏，身份hash為排序後的 MARKET:SYMBOL 字串列表之緊湊JSON（UTF-8）SHA256，並核對 identity_count／horizon_count。分片入口沒有額外授權，也不可讀取未宣告私有路徑。
5. 報告保留新進場、若已持有、正反理由、衝突、日期及季度／長期缺項。任何未完成讀取應回報精確缺片／缺身份，不能退回補充名單後聲稱完整研究。

每個新增judgment分片最多200,000 bytes；索引與重用預測檔最多300,000 bytes。索引包含all_analysis來源hash及每片hash。預測檔還須與all_analysis記錄的來源hash一致，防止同日期不同內容混合。產出前驗證完整池及每個期間的日期、模型版本、建立時間、預期報酬對應；缺漏時發布unavailable索引。分片先寫、索引最後原子替換，中斷或跨commit讀取由批次／hash驗證拒絕；重跑可恢復。舊分片未被索引列出就不得讀入。

公開新投影不包含原列的price、買賣價位、私有持倉或憑證。judgment使用明確欄位白名單，保留execution_authorized=false、holdings_assumed=false與空機率／目標價。引用的預測檔早已公開；不複製其中原始價格欄位。來源限制、進場／失效規則與投資效能未驗證等限制保持原樣。

驗收分成程式測試、既有產報真正發布，以及原生任務完整讀取三步；後兩者不可由測試或後端筆數代替。本修正不修改原生任務、不新增排程、不手動產報或通知。
