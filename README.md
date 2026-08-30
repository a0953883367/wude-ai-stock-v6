# 武得 AI 股票助理 V6.32

台股＋美股 AI 選股系統，以及每天自動執行的「AI 股票早、中、晚報」。GitHub Pages 手機版儀表板、ChatGPT 股票助理與 Telegram 使用同一份固定觀察清單。

## 手機版儀表板

- 顯示台股、美股、ETF TOP 20
- 新增「我的固定清單」
- 新增早報、午報、晚報三個切換按鈕
- 顯示紅黃綠燈、現價、漲跌、量能、買價、支撐、壓力與風險
- 搜尋可查代號或中文名稱
- 每次開啟頁面都以 no-store 方式讀取最新資料

## 自動報告時間（台灣時間）

- 06:00 早報
- 12:00 午報
- 20:00 晚報

每次執行會保存 reports/morning.json、reports/noon.json 或 reports/evening.json，同時更新 reports/latest.json，讓網頁可分別查看三個時段。

GitHub Actions 會把固定觀察清單完整列入 Telegram 報告，依代號去重；同時保留全台股背景掃描，另外列出最強 5 檔。暫時抓不到可靠行情的標的會列在「暫無可靠行情」，不會無聲消失。

## 富邦本機行情自動化（Windows）

富邦憑證與密碼只留在你的電腦。程式會自動讀取：

- Windows 認證 `FUBON_API_WUDE`
- Windows 認證 `FUBON_CERT_WUDE`
- `%LOCALAPPDATA%\WudeAI\cert\fubon_cert.p12`

完成上述設定及官方 Fubon Neo SDK 安裝後，在 PowerShell 執行一次：

```powershell
powershell -ExecutionPolicy Bypass -File .\setup_fubon_windows.ps1
```

腳本會先驗證登入與 2330 唯讀行情，再建立 06:00、12:00、20:00 三個排程。電腦需在執行時間開機及連網；若錯過時間，Windows 會在下次可執行時補跑。排程不包含下單功能。

## 手機立即更新與即時模式

手機點開個股的「自動即時判斷」後，可使用：

- `立即更新`：單次取得該檔最新授權行情。
- `即時模式`：頁面開啟期間每 3～30 秒更新一次，預設 5 秒。
- 關閉個股視窗後會停止秒級請求；早中晚報與背景掃描仍照原排程。

`live_api.py` 是獨立的 owner-only 雲端資料層。美股從 Alpaca SIP／OPRA
取得資料；台股從 Fubon Neo 取得資料。所有金鑰及富邦憑證只能存於雲端
secret store，公開 GitHub Pages 只接收計算結果。服務未部署或暫時失敗時，
畫面保留最近一次背景快照，不會把舊資料偽裝成即時行情。

即時逐筆成交另有 `capital_flow_shadow.py` 影子帳本。台股與美股完全分開，
各自輸出 1／5／15／60 分鐘的主動買賣估算、市場廣度、ETF／個股、族群共振、
個股流入與流出。美股會處理 SIP 成交更正、取消與不適合判斷即時方向的特殊
成交條件。這些結果只代表成交方向推估，不能識別外資、法人、主力或真實帳戶，
也不會更動 V6、ALL、TOP10、正式排名或下單邏輯。

逐筆帳本另按台股／美股正常盤累積每日封存；未收盤的內容不對日報輸出。
只有涵蓋開盤、收盤且至少 10 檔有有效方向成交的同日資料，才會在正式晚報
（台股）或早報（美股）以 15% 權重加入「族群輪動分」；缺漏、跨市場或日期
不一致時維持原輪動分數，不把資料不足誤判成資金流出。A 組正式 V6 不受影響。

雲端服務至少需要下列 secrets：

- 台股：`FUBON_ID`、`FUBON_API_KEY`、`FUBON_CERT_PASSWORD`、
  `FUBON_CERT_BASE64`，以及官方 Fubon Neo SDK。
- 美股：`ALPACA_API_KEY_ID`、`ALPACA_API_SECRET_KEY`；有 OPRA 權限時再設定
  `ALPACA_OPTION_FEED=opra`。
- 存取保護：`LIVE_ACCESS_TOKEN` 或由私人網站／存取閘道注入
  `LIVE_TRUSTED_AUTH_HEADER`。除非已確認行情授權與流量限制，禁止設定
  `LIVE_PUBLIC_READ=1`。

正式 Railway 後端網址已寫入 `live_config.js` 的 `WUDE_LIVE_API_BASE`；只有更換
主機時才需調整。這個檔案只能放網址與更新秒數，絕對不能放任何憑證。

### Railway 一個月完整試用

本專案已提供 `Dockerfile` 與 `railway.json`。Railway 會自動安裝官方 Linux
版 Fubon Neo SDK v2.2.9，並以 `/health` 驗證服務；固定只啟動一個 replica。

Railway Variables／Secrets 必須設定：

- `FUBON_ID`
- `FUBON_API_KEY`
- `FUBON_CERT_PASSWORD`
- `FUBON_CERT_BASE64`（既有 `.p12` 憑證的 base64；不能提交到 GitHub）
- `ALPACA_API_KEY_ID`
- `ALPACA_API_SECRET_KEY`
- `ALPACA_STOCK_FEED=sip`
- `ALPACA_OPTION_FEED=opra`
- `LIVE_ACCESS_TOKEN`（至少32位元組的隨機值）
- `LIVE_ALLOWED_ORIGINS=https://a0953883367.github.io`
- `LIVE_PUBLIC_READ=0`
- `LIVE_MAX_REQUESTS_PER_MINUTE=120`

部署後建立一次性私人網址：

```text
https://a0953883367.github.io/wude-ai-stock-v6/#live_token=<LIVE_ACCESS_TOKEN>
```

手機第一次開啟會把 token 留在該裝置，隨即從網址列移除；往後不必重新輸入。
Token 不會寫入 GitHub、`live_config.js` 或報告檔。若手機遺失，立即更換
`LIVE_ACCESS_TOKEN` 即可讓舊連結失效。

瀏覽器請求使用 `X-Live-Token` 標頭；不要改成 `Authorization: Bearer`，
因為 Railway 的擁有者驗證會先攔截該標頭。

## 2萬元台／美股交易控制

主版提供 owner-only 交易清單，台股與美股合計最多勾選3檔，共用同一筆新台幣
資金。硬上限為20,000元，預留10%（至少500元），其餘依勾選檔數平均分配；
未通過隔日、進場區與風控條件的標的會保留現金，不會把預算轉去加碼其他標的。

正式部署預設只啟用 paper mode：模擬成交、核對持倉、停損、移動停利與最長5個交易日。
美股部位以最新美元兌台幣換算，並預留交易與匯兌成本。富邦 Neo 官方行情文件
標示為台灣證券市場，因此 `fubon_broker.py` 的真單介面目前只準備台股且維持
fail-closed；美股真單不得經由未確認支援的介面送出。

台股真單引擎會先核對富邦委託書號、部分成交、可委託庫存與成交價金；只有成交
股數會成為系統持倉。賣單同樣必須核對成交後才會減少持股。本人頁面在 live mode
會顯示一個「開啟真實下單」總開關；按一次確認後，勾選股票即同步至交易檢查，
合格才送限價買單，不合格則保留現金。取消勾選會要求取消尚未成交的買單。

Railway 紙上測試可先使用暫存狀態。任何真單測試前必須掛載永久 Volume，並設定
`TRADE_STATE_PATH=/data/trading_state.json`；若路徑不在 `/data/`、環境模式
不是 `live`、開關未啟用或伺服器確認值不一致，真單適配器都會拒絕委託。
模擬與真單不得共用同一個狀態檔。即使程式已部署，`TRADING_MODE=paper` 與
`LIVE_TRADING_ENABLED=false` 仍會讓富邦真單維持關閉。

隔日預測 V3 只在市場正式收盤後保存快照：台股使用晚報、美股使用次日早報，
並以資料源提供的正式交易日、調整後開盤價與調整後收盤價對齊。同市場、同股票、
同交易日只保存一次；舊版只比較收盤或以報告時間推算交易日的紀錄不再參與統計。

`performance.json` 會分開保存「今日收盤→次日開盤」、「次日開盤→次日收盤」
與「今日收盤→次日收盤」，另保留 5／10／20 個交易日收盤結果，並以十個透明
候選模型進行 shadow test。報表同時顯示方向命中率、樣本數、訊號涵蓋率、方向
調整後平均報酬與最差報酬。累積未達 60 個交易日且 200 筆共識訊號前，結果一律
不影響 AI 分數；達標後也只取得模型遴選資格，不得自動改動正式排名。

## 通知設定

Repository Settings → Secrets and variables → Actions：

- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
- FINMIND_TOKEN（選填；填入後才會加入外資、投信、自營商資料）

## 美股權威即時資料（選填）

美股模型不套用台股法人欄位。若要啟用全市場 SIP 與 OPRA 風險層，請在
Repository Settings → Secrets and variables → Actions 設定：

- Secret `ALPACA_API_KEY_ID`
- Secret `ALPACA_API_SECRET_KEY`
- Variable `ALPACA_STOCK_FEED`：`sip`
- Variable `ALPACA_OPTION_FEED`：有 OPRA 訂閱時填 `opra`，否則留空

沒有設定授權時，系統保留 Yahoo／SEC／FINRA 備援並降低資料涵蓋，不會把
IEX、延遲或指示性報價標示成 SIP／OPRA。

本系統為資料整理與風險輔助，不保證獲利，也不是代客下單建議。
