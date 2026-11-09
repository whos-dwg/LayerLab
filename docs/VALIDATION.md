# 發布驗證紀錄

驗證日期：2026-11-05（Asia/Taipei）。原始驗證使用當時工作目錄的程式；本次維護更新同步至公開預覽版本。採全新 Python 虛擬環境安裝，Windows／AutoCAD 為既有主機環境，未宣稱全新作業系統驗收。

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

本次已修正成功提示：DXF 提示下載驗證後檔案；DWG 保留選擇儲存位置提示。核心、預覽及完整 DOM 回歸重新執行，結果見本次維護紀錄。


## 2026-11-05 維護驗證

JavaScript 語法檢查通過；核心與預覽 19/19、完整 DOM 9/9 通過。新增格式分流測試，實際完成模擬 DXF 與 DWG 轉換後，確認提示、下載連結及儲存按鈕正確。原生 CAD 引擎未更動，本輪未重跑 CAD 轉換。


## v1.0.0-preview.2 發布前檢查（2026-11-09）

- Python 35 項通過；其中 Git hook 首次被沙箱 MSYS 權限阻擋，於沙箱外重跑該套件 10/10 通過。新增 DWS 下載拒絕測試 1 項通過。
- Node 核心、預覽與完整 DOM：29/29 通過。
- 原生引擎重新建置：0 errors、3 warnings；合成 DWG 掃描與三程序轉換 PASS。18 → 18，非圖層簽章一致，來源未改變，5 個映射來源層已移除。
- 本輪未進行 DWS 桌面載入、模型服務及大型圖面實測；舊版證據保持其原適用範圍。
- 截圖沿用前版合成示範，不代表新功能已做視覺驗收。


## 建置警告修正與 DWS 桌面載入（2026-11-09）

三項 MSB3277 分別為 Microsoft.VisualBasic、System.Drawing、WindowsBase 的 Windows Desktop 組件參考衝突。原生專案新增 Microsoft.WindowsDesktop.App 框架參考，讓組件由匹配的 targeting pack 解析；未加入 NoWarn、未移除既有檢查。最終 Release 完整重建使用 `--no-incremental -warnaserror`，結果 0 warnings、0 errors。

使用既有合成 DWG 與原始核准映射完成 DWS 建立、AutoCAD 2026 桌面 `acet-laytrans` 實際載入及儲存，最後以獨立 Core Console 重開驗證。結果為 PASS、actual_autocad_load=true、18 → 18、4 個目標圖層屬性正確、5 個來源層移除；合成來源與已驗證主 DWG 雜湊保持一致。

原桌面文件的啟用文件、文件清單、DBMOD、空選取與空命令狀態在前後快照一致。原文件磁碟讀取受 AutoCAD 鎖定，本輪不宣稱已對其磁碟內容做前後雜湊比對。只操作合成副本，未儲存或關閉原文件。

首次誤用執行收據 mapping.json（欄位大小寫與核准映射不同），導致建立命令失敗；此失敗保留於本機紀錄，未採認為通過。改用正式 API 同一路徑的 approved-mapping.json 後完整流程通過。臨時診斷 trace 已撤除，原生演算法未改動。DWS 7 項契約與下載保護回歸通過。

[合成 DWS 實際載入報告](validation/dws-desktop-validation.json)。本輪證據不涵蓋大型圖面、XREF、尺寸匿名快取或含逐物件例外的 DWS；最後一種仍明確拒絕匯出。


## v1.0.0-preview.4 初始候選驗證（2026-11-09，歷史階段）

- 補齊公開包缺少的三個 Git hooks 及 LF 設定，驗收對象改為候選 ZIP 解壓後的完整檔案。
- 修正自訂 annotation 分類的目標查找；文字／尺寸不再只能依 text／dim 後綴選擇。保留專用目標區分及多候選待判讀。
- 原生收據改用 `map_editable_preserve_xref_verify_owning_dimensions_exclude_regenerated_caches`，與實際驗證政策一致。
- 最新 adapter 以 `--no-incremental --no-restore -warnaserror` 完整重建，0 警告、0 錯誤。
- 一般整層映射與系統 0 層映射均重新完成 DWG 三程序及 DWS 桌面實際載入／儲存／獨立 Core Console 重開驗證：18 → 18；一般案例驗證 18 個映射物件、4 個目標及 5 個來源層移除，0 層案例驗證 3 個映射物件及 2 個來源層移除。合成來源及兩份已驗證主 DWG 雜湊未變。
- 最初桌面快照的文件列舉不完整，另有一次快照讀取遭短暫 RPC 忙碌拒絕；這些未作為桌面狀態通過證據。最後以包含啟用文件狀態的前後快照，針對性重驗一次 DWS 流程；啟用文件、完整文件清單、DBMOD、選取與命令狀態一致，沒有殘留測試副本。原開啟文件受鎖定，未宣稱其磁碟雜湊已前後比對。

[最新合成 DWG 報告](validation/dwg-preview4-validation.json) · [最新合成 DWS 桌面報告](validation/dws-desktop-validation.json)。這次未新增模型 API 或大型圖面驗收；私人原圖及桌面原始快照不納入候選包。


候選 ZIP 解壓驗收：Python 61/61 通過（包含三個 Git hooks 的實際同步測試）、Node 核心／預覽／DOM 31/31 通過；pip check、CLI health／參數、HTTP 工作台、合成示範 8 層／18 物件／2 保護層、核准映射匯出、DXF Save/Reopen 及三個結果下載均通過。Git hook 測試使用允許 MSYS 執行的隔離暫存倉庫；未對使用者工作倉庫安裝 hooks。驗證服務已停止。

最後打包僅補寫上述文件與測試結果，程式及測試檔案與解壓驗收版本相同。當時候選包仍待使用者實測，未建立 GitHub preview.4 標籤或 Release；目前採用下方回修與最終確認結果。


### 發布前 DWS 實測回修

初始合成案例未涵蓋 Layer Translator 仍保留來源層的情況。實測另外發現此條件後，保留先前的尺寸快取排除修正，新增僅作用於可丟棄驗證副本的有界來源層清理，再以另一個程序重開完整驗證。清理只處理仍存在且列於原核准清單的圖層，不使用 Database.Purge；DWS 清理／驗證的受控錯誤會寫入 trace 並缺少成功 marker，因此不交付未驗證 DWS。

本機先前失敗及部分完成紀錄均保留；實際工作原圖、公司資料、原始 trace 及其報告不加入候選包。公開驗證報告只保留重新執行的合成案例。

回修候選包解壓後 Python 64/64 通過；介面檔案與先前 31/31 前端通過版本相同。最新一般／系統 0 層合成 DWS 均重驗 PASS、18 → 18，包含新增的有界清理與獨立最終回讀。先前實際失敗工作的本機重驗亦取得 PASS；其原圖與原始報告不公開。


### 最終使用者實測與發布確認（2026-11-09）

使用者已確認 DWS 實測正常並授權發布 v1.0.0-preview.4。發布包相對此實測候選版本只更新文件狀態與下載入口，程式及測試檔案完全相同；保留上述初始候選、失敗與回修的適用範圍，不把初始合成通過擴稱為所有圖面通過。
