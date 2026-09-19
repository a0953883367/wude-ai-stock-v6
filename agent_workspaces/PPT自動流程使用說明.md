# PPT 自動流程

`presentation_pipeline.mjs` 以同一份 JSON 規格完成：

- 新增可編輯的 PPTX。
- 匯入既有 PPTX 並以精確文字替換方式編輯。
- 執行封裝、字型、版面及第一方重新匯入驗證。
- 逐頁輸出 PNG，並另外匯出 PDF 與 HTML。

輸入可參考 `PPT自動產生規格範本.json`；正式內容應列出來源檔案。中文簡報需在執行環境安裝中文字型，並於規格加入 `fontFamily`。無論自動驗證是否通過，正式交付前仍需逐頁檢查畫面與來源內容。

建立模式：

```text
presentation_pipeline.mjs --mode create --spec /絕對路徑/spec.json --output /絕對路徑/report.pptx --formats pptx,pdf,html
```

編輯模式：

```text
presentation_pipeline.mjs --mode edit --source /絕對路徑/source.pptx --spec /絕對路徑/edit.json --output /絕對路徑/revised.pptx --formats pptx,pdf,html
```

付款、寄送及公開發布不屬於此流程，也不會自動執行。
