# GitHub 發布內容

本專案已採 MIT 授權並公開發布 [v1.0.0-preview.1](https://github.com/whos-dwg/LayerLab/releases/tag/v1.0.0-preview.1)。本次維護修正 DXF 成功提示，更新文件及附件，並將 Release 標記為 Pre-release。

## Repository About

**名稱建議：** LayerLab

**Description：** Evidence-based DWG/DXF layer standardization with a local review workbench, explicit human approval, and native AutoCAD Save/Reopen verification.

**Topics：** `autocad`, `dwg`, `dxf`, `cad`, `layer-management`, `python`, `fastapi`, `human-in-the-loop`, `windows`

## Release 標題

LayerLab — 首次公開預覽

版本標籤為 `v1.0.0-preview.1`。本次更新會同步標籤、原始碼及發布附件。

## Release 內文

LayerLab 提供 Windows 本機 CAD 圖層審核工作台，協助將來源 DWG／DXF 依內容證據映射至使用者核准的標準。

### 本次提供

- 原生 AutoCAD 2026 DWG 盤點與圖層副本轉換。
- 匿名化的 47 層建築預設標準，以及完整判讀、建議與驗證機制。
- 來源與標準雙側清單、幾何預覽、獨立搜尋及跨頁批次操作。
- 建議、暫定映射與人工核准分開，所有非保護層需明確決策。
- 瀏覽器草稿保存、恢復指紋核對及多分頁衝突提示。
- 可選模型語意建議與獨立稽核資料。
- 兩個 AutoCAD 程序完成另存、重開及驗證；另有 DXF 相容流程。

### 驗證結果

2026-11-05 在 Windows、Python 3.13.13、AutoCAD 2026 完成全新 venv 安裝與示範工作台流程；DXF 與原生 DWG 均通過 Save/Reopen 驗證，18 個物件保持一致。47 項核心、預覽、writer、同步及 workflow 檢查通過。詳見 [驗證紀錄](VALIDATION.md)，含驗證範圍與已知限制。

### 授權

MIT License。

### 開始使用

從儲存庫首頁 README 的快速開始安裝 Python 相依套件。原生 DWG 另需 AutoCAD 2026 與 .NET 10 SDK。先用示範標準了解流程，再載入正式核准標準。

### 已知限制

目前以 Windows 與 AutoCAD 2026 為原生整合環境，安裝路徑含固定設定。DXF writer 不修改圖塊定義。草稿只屬於目前瀏覽器與網址。模型功能需自行設定 API 並確認證據資料可傳送。內附匿名化的 47 層預設標準；不含公司名稱及實際專案圖面。

### 問題回報

請透過儲存庫 Issues 提供去識別的最小重現案例、環境版本、操作步驟及錯誤訊息。

## 發布前完成

- [x] 權利人選擇 MIT，已加入 LICENSE。
- [x] 已確認公開儲存庫與版本標籤。
- [x] 僅發布白名單內容與公開歷史，保留已獲同意的匿名化預設標準。
- [x] 在既有 Windows 主機的全新 Python venv 完成安裝、示範與原生 DWG 流程；非全新 OS 驗收。
- [x] 已加入 docs/VALIDATION.md 與去識別驗證報告。
- [x] README 加入審核中與驗證完成的實際示範截圖。
- [x] 原始碼 ZIP 使用檔案白名單，不含 AutoCAD DLL、安裝程式、客戶圖面或虛擬環境。
- [x] 公開預覽採 Pre-release 標記。
