# LayerLab v1.0.0-preview.4

本版接替 v1.0.0-preview.3，補齊發布包缺件，修正自訂標準辨識及 DWS 驗證流程。使用者已實測確認 DWS 正常。

## 修正與變更

- 補齊三個 Git hooks 與 LF 設定；更正公開包測試腳本的文件說明。
- 修正 STD-ANNOTATION 等自訂文字／尺寸目標無法獲得建議的退步；多候選仍保留待判讀，建議仍需人工核准。
- 納入名稱／內容辨識、重新判讀及保留相容草稿；匿名化預設標準含 48 個目標（含系統 0 層）。
- 保留先前排除重建尺寸快取、驗證所屬 Dimension 物件的修正，並使收據政策與實際行為一致。
- DWS 載入後，只在可丟棄驗證副本清理仍存在的核准來源層，再由另一個新程序重開完整驗證。失敗不交付 DWS，不影響已驗證主 DWG。

## 驗證

- 原生 adapter 完整重建：0 警告、0 錯誤。
- 最終候選 ZIP 解壓後 Python 64/64 通過；前端檔案與 31/31 通過版本相同。
- 一般與系統 0 層合成 DWS 均通過實際 AutoCAD 桌面載入、清理與獨立重開驗證，18 → 18；來源及主 DWG 雜湊未變，桌面狀態核對一致。
- 工作台啟動、合成 DXF、核准映射匯出、Save/Reopen 與下載通過。使用者另已完成 DWS 實測。
- 62 個公開檔案重新檢查個資、公司名稱、私人路徑與憑證格式；私人工作圖面、輸出、桌面快照、原始 trace 與內部 Git 歷史均不納入。

## 安裝與限制

原生路徑需要 AutoCAD 2026 Update 1.2 以上與 .NET 10 SDK。DWS 僅適用整層映射，不支援逐物件例外；手動重用 DWS 可能保留來源空層，主要標準化 DWG 仍為完整驗證成果。模型 API、XREF 與尺寸快取的其他案例未在本輪新增驗收；本機實際工作原圖及報告不公開。

[安裝與使用](https://github.com/whos-dwg/LayerLab/blob/v1.0.0-preview.4/README.md) · [驗證紀錄](https://github.com/whos-dwg/LayerLab/blob/v1.0.0-preview.4/docs/VALIDATION.md) · [隱私檢查](https://github.com/whos-dwg/LayerLab/blob/v1.0.0-preview.4/docs/PRIVACY_REVIEW.md)

MIT License。本版仍為 Pre-release。
