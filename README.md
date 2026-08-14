# 武得 AI 股票助理 V6

台股＋美股 AI 選股系統，以及每天自動執行的「AI 股票早、中、晚報」。原有的 `index.html`、`stock_data.json` 與 `search_data.json` 保持不變，GitHub Pages 可繼續使用。

## 自動報告時間（台灣時間）

- 06:00 早報
- 12:00 午報
- 20:00 晚報

GitHub Actions 每次會讀取 `search_data.json` 的完整台股候選池，重新計算相對量能、5 分 K 攻擊量代理值、量價、法人、族群共振、均線與支撐壓力，再產生動態 TOP 10；不會沿用開盤前的固定排名。

報告會寫入：

- `reports/latest.md`
- `reports/latest.json`
- `reports/archive/`（保留近期紀錄）

## 第一次啟用通知

到 GitHub repository 的 `Settings` → `Secrets and variables` → `Actions` → `New repository secret`，加入：

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_CHAT_ID`
- `FINMIND_TOKEN`（選填；填入後才會加入外資、投信、自營商資料）

沒有 Telegram 設定時，程式仍會正常產生報告，不會因為未設定通知而失敗。

## 手動測試

在 repository 上方選 `Actions` → `AI 股票早中晚報` → `Run workflow`，選擇 `morning`、`noon` 或 `evening`。

本系統為資料整理與風險輔助，不保證獲利，也不是代客下單建議。

