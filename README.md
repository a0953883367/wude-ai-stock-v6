# 武得 AI 股票助理 V6

台股＋美股 AI 選股系統，以及每天自動執行的「AI 股票早、中、晚報」。原有網頁檔案維持不變，GitHub Pages 可繼續使用。

## 自動報告時間（台灣時間）

- 06:00 早報
- 12:00 午報
- 20:00 晚報

GitHub Actions 每次會把 ChatGPT 股票助理的同一份固定觀察清單完整列入 Telegram 報告，依代號去重；同時保留全台股背景掃描，另外列出最強 5 檔。固定清單包含台股、美股與 ETF；暫時抓不到可靠行情的標的會明確列在「暫無可靠行情」，不會無聲消失。

每檔顯示紅黃綠燈、現價、漲跌、相對量能、參考買價、支撐、壓力、操作判斷與風險。Telegram 使用手機友善的純文字卡片格式，不再顯示原始 Markdown 的井字標題。

報告會寫入：

- reports/latest.md
- reports/latest.json
- reports/archive/（保留近期紀錄）

## 第一次啟用通知

到 GitHub repository 的 Settings → Secrets and variables → Actions → New repository secret，加入：

- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
- FINMIND_TOKEN（選填；填入後才會加入外資、投信、自營商資料）

沒有 Telegram 設定時，程式仍會正常產生報告，不會因為未設定通知而失敗。

## 手動測試

在 repository 上方選 Actions → AI 股票早中晚報 → Run workflow，選擇 morning、noon 或 evening。

本系統為資料整理與風險輔助，不保證獲利，也不是代客下單建議。
