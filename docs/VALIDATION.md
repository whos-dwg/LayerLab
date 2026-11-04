# 發布驗證紀錄

驗證日期：2026-11-05（Asia/Taipei）。使用目前工作目錄的程式；不是已發布的 Git tag。採全新 Python 虛擬環境安裝，Windows／AutoCAD 為既有主機環境，未宣稱全新作業系統驗收。

## 環境與結果

| 項目 | 結果 |
| --- | --- |
| Python | 3.13.13；全新 venv 安裝 requirements.txt 成功；pip check 通過 |
| 套件版本 | 見 requirements-validated.txt，為本次驗證快照 |
| Node.js | v24.22.1 |
| .NET SDK | 10.0.403；原生 adapter Release 建置成功，0 errors、3 warnings |
| AutoCAD | 2026 Core Console，W.179.0.0 |
| CLI health | DWG／DXF scan、convert 可用；此檢查僅確認可用性 |
| Web 啟動 | uvicorn、127.0.0.1:8877；Chrome 實際連線與操作成功 |
| 前端核心與預覽測試 | 19/19 通過 |
| 原生 writer 契約測試 | 6/6 通過 |
| UI 同步測試 | 10/10 通過 |
| UI workflow 契約測試 | 4/4 通過 |
| DXF CLI | scan → suggest → convert 成功 |
| 原生 DWG CLI | 建置 → scan → convert → 獨立程序重開驗證成功 |

## 實際瀏覽器操作

以內附 demo_input.dxf 與 DEMO_STANDARD.json 執行。載入後為 8 層、18 個物件、6 個可審核來源及 2 個保護層。逐層確認 A-WALL、WALL-NEW、A-DOOR、A-TEXT、MISC-05 的建議映射，EMPTY-01 確認保留；完成後為 6/6 已審核、0 待審。匯出映射並執行另存 DXF，結果為 PASS、18 → 18、來源未改變、圖塊定義保留。截圖為此實際流程，未使用客戶圖面。

CLI DXF convert 使用上述人工完成的映射。DWG 樣本由同份示範 DXF 透過 AutoCAD 另存產生，重新盤點後將同份決策綁定 DWG 的 SHA-256 再轉換。DWG 結果為 PASS、18 → 18、非圖層簽章一致、來源未改變；映射涵蓋 17 個 model 物件及 1 個 block_definition 物件。

去除本機絕對路徑的實際報告：

- [DXF validation](validation/dxf-validation.json)
- [DWG validation](validation/dwg-validation.json)

## 發現與處理

- `run.py serve` 會偵測本機既有正式 skill 設定，並拒絕示範標準。README 的獨立示範改用直接 uvicorn 啟動，正式 skill 仍沿用原入口。
- 8765 在驗證主機已被既有服務使用；未停止既有服務，實測使用 8877，README 已加入連接埠替換說明。
- 沙箱首次阻止 PyPI 連線、本機 socket 與 Git MSYS hook。允許執行後，套件安裝、Web 操作與同步測試均通過。
- 原生建置有 MSB3277 組件版本衝突警告，主要涉及 Microsoft.VisualBasic／System.Windows.Extensions；本次樣本的掃描與兩程序轉換成功，但尚未消除警告。

## 驗證範圍

本次樣本不含 XREF、尺寸匿名快取或大型複雜圖面，不能將結果延伸為這些案例已完成驗收。模型 API、原生 DWG 的瀏覽器檔案選擇與下載儲存對話框未在本輪執行；DWG 實際轉換使用 CLI。完整 DOM 回歸另使用本機隔離的 jsdom 依賴執行，已改用完全合成標準，8/8 通過。本機已存在的其他未提交修改保留，發布包包含目前 source 的實際內容。

已知介面小問題：DXF 成功後的短暫 toast 仍提示選擇 DWG 儲存位置；持續顯示的成功面板與下載連結正確標示 DXF。本次未更動介面程式。
