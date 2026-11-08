# 參與 LayerLab 開發

請先閱讀 [README](README.md) 與[資料契約](src/dwg-layer-standardizer/references/CONTRACTS.md)。本專案採 MIT 授權，提交內容請確保有權依此授權提供。

## 回報問題

請在 GitHub Issues 提供操作步驟、預期與實際結果、Windows／Python／AutoCAD 版本、輸入格式及已去識別的錯誤訊息。請使用可公開的最小重現圖面，避免上傳客戶原圖、正式公司標準、API 金鑰或含專案文字的盤點資料。

## 提交變更

建立分支，讓每個 PR 聚焦一個問題。說明使用者可觀察的行為變化、驗證方式及限制。變更 JSON 格式時，同步更新契約與範例。

請保留來源完整性、明確人工核准、保護圖層及副本驗證等既有規則。介面變更需附去識別的前後畫面與鍵盤操作檢查。

## 驗證

公開預覽原始碼包與目前公開倉庫不含內部 `tools/` 測試腳本。公開包可依 README 執行 `pip check`、CLI `health` 與合成 DXF 示範；以下命令僅適用另外具備完整測試來源的開發副本。於該副本根目錄執行與變更相關的檢查：

```powershell
node --test tools/test-workbench-core.cjs
node --test tools/test-preview-hover.cjs
python -m unittest discover -s tools -p test_native_writer_contract.py -v
python -m unittest discover -s tools -p test_ui_sync.py -v
```

完整 DOM 互動測試的隔離依賴與命令見操作手冊。原生 DWG 變更還需要在 AutoCAD 2026 環境執行實際副本轉換、來源層清理與最終重開驗證三程序；單元測試不能取代 CAD 驗證。請在 PR 註明實際執行結果與未執行項目。
