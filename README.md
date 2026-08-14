# 武得 AI 股票助理 V6.15

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

## 通知設定

Repository Settings → Secrets and variables → Actions：

- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
- FINMIND_TOKEN（選填；填入後才會加入外資、投信、自營商資料）

本系統為資料整理與風險輔助，不保證獲利，也不是代客下單建議。
