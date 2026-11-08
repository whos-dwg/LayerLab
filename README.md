<div align="center">

# LayerLab

**讓每一次 CAD 圖層標準化，都有證據、人工決策與驗證紀錄。**

Windows 本機圖層審核工作台 · AutoCAD 2026 原生 DWG · DXF 相容流程

[快速開始](#快速開始) · [操作手冊](src/dwg-layer-standardizer/README.zh-TW.md) · [資料契約](src/dwg-layer-standardizer/references/CONTRACTS.md) · [參與開發](CONTRIBUTING.md)

</div>

LayerLab 協助整理來自不同顧問、廠商與專案的 DWG／DXF 圖層。先讀取圖面內容，再依使用者提供的標準建立映射建議；由人員確認每個來源層的去向，最後另存副本並驗證結果。

圖層名稱只是線索。物件類型、文字、圖塊、空間分布與幾何預覽，才是判讀依據。

*下方截圖拍攝於前一版，用於說明基礎審核流程；新功能以操作手冊為準。*

![LayerLab 工作台：示範圖面的圖層內容與映射判讀](docs/images/workbench-review.jpg)

*使用儲存庫內附示範 DXF 與示範標準拍攝。*

## 目錄

- [功能](#功能)
- [處理流程](#處理流程)
- [快速開始](#快速開始)
- [命令列操作](#命令列操作)
- [設定與資料](#設定與資料)
- [限制與保護](#限制與保護)
- [開發與文件](#開發與文件)
- [授權](#授權)

## 功能

| 功能 | 使用方式與價值 |
| --- | --- |
| 原生 DWG 盤點 | 透過 AutoCAD 2026 讀取模型空間、配置及圖塊定義，不經 DXF 中轉 |
| 內容證據 | 檢視圖層屬性、物件類型、圖塊與文字樣本、幾何預覽 |
| 映射編排台 | 來源與目標獨立搜尋，支援跨頁勾選、批次指定與單層操作 |
| 明確人工核准 | 建議、暫定目標與已確認決策分開；拖放不等於核准 |
| 審核進度保存 | 瀏覽器保存草稿；恢復時核對盤點與標準指紋，並處理分頁衝突 |
| 可選模型建議 | 設定 API 後分析有界內容證據，保留稽核 JSON；仍需人工採用 |
| 副本與驗證 | DWG 依序經獨立程序寫入、重開清理來源層、再次重開驗證 |
| DXF 相容 | 使用 ezdxf 產生副本並回讀驗證 |

逐物件判讀可依文字、尺寸、圖塊語意與有限幾何證據提出建議；可選 DWS 匯出需另通過 AutoCAD 桌面實際載入驗證，失敗不影響已驗證 DWG。

## 處理流程

```mermaid
flowchart LR
    A[DWG / DXF] --> B[內容盤點]
    B --> C[載入核准標準]
    C --> D[建議與幾何證據]
    D --> E[人工逐層或批次確認]
    E --> F[另存新圖面]
    F --> G[重開驗證]
    G --> H[驗證結果與稽核資料]
```

例如，同樣名為 `WALL` 的來源層，仍需檢視內容並指定公司標準中的目標層；選擇目標後先成為暫定映射，確認後才可進入轉換。

## 快速開始

### 環境需求

- Windows；原生 DWG 流程需 AutoCAD 2026 Update 1.2 或更新版本（.NET 10）。
- Python 與 pip；本次已在 Python 3.13.13 驗證。其他版本尚未驗證。
- 建置原生 adapter 需要 .NET 10 SDK；目前專案目標為 `net10.0-windows`、x64。
- 原生整合目前使用 `C:\Program Files\Autodesk\AutoCAD 2026`；其他安裝位置需調整整合程式與參考路徑。
- Node.js 僅用於前端檢查及測試。

下載儲存庫 ZIP 並解壓，於專案根目錄開啟 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r src/dwg-layer-standardizer/requirements.txt
$env:CADLAYER_STANDARD = (Resolve-Path "src/dwg-layer-standardizer/standards/DEMO_STANDARD.json").Path
.\.venv\Scripts\python.exe src/dwg-layer-standardizer/scripts/run.py health
.\.venv\Scripts\python.exe -m uvicorn cadlayer.app:app --app-dir src/dwg-layer-standardizer --host 127.0.0.1 --port 8765
```

保持 PowerShell 開啟，手動在瀏覽器開啟 `http://127.0.0.1:8765`；結束時於 PowerShell 按 Ctrl+C。若 8765 已被使用，將命令的連接埠與瀏覽器網址一併改為 8877。獨立示範使用 uvicorn 啟動，可避免本機既有 skill 的正式標準同步設定介入。正式已安裝 skill 仍使用操作手冊的 `run.py serve`。

若要重現本次套件版本，可將安裝命令的 requirements 路徑改為 `docs/requirements-validated.txt`。

上例使用內附示範標準；正式作業前，請將 `CADLAYER_STANDARD` 改為已核准標準 JSON 的絕對路徑。另內附 47 層的匿名化預設標準 `standards/DEFAULT_STANDARD.json`，保留圖層名稱、CAD 屬性、別名、分類與命名規則，不含公司名稱及來源工作紀錄。

1. 點選「開啟示範 DXF」，或匯入 DWG、DXF／`cad-layer-inventory/v1` 盤點 JSON。
2. 檢查目前標準，查看來源層的內容證據與幾何。
3. 指定目標並確認映射，或明確選擇保留；完成所有非保護來源層的決策。
4. 匯出映射供留存，再執行另存與驗證。
5. 檢查驗證報告及輸出圖面。

### 完整示範操作

示範載入後應顯示 **8 個來源層、18 個物件、6 層待審、2 層保護**。逐一選取 `A-WALL`、`WALL-NEW`、`A-DOOR`、`A-TEXT`、`MISC-05`，核對證據後按各層「確認」；`EMPTY-01` 選擇「保留目前圖層」。此時應為 6/6 已審核、0 待審。

點選「匯出映射」，由結果連結下載 Mapping JSON；再點「另存 DXF 並驗證」。成功後顯示物件數 18 → 18、原始檔未修改，並提供輸出 DXF、mapping.json 與 validation.json 下載連結。

![示範 DXF 已另存並通過 Save/Reopen 驗證](docs/images/workbench-verified.jpg)

最新建置已達 0 警告、0 錯誤；合成樣本 DWS 桌面實際載入與獨立重開驗證通過。

本次實際驗證範圍、套件版本與限制見[驗證紀錄](docs/VALIDATION.md)。

## 命令列操作

以下命令從 `src/dwg-layer-standardizer` 目錄執行，並使用已安裝相依套件的 Python：

```powershell
python scripts/run.py health
python scripts/run.py scan --source input.dwg --standard standard.json --out scan-job
python scripts/run.py suggest --inventory scan-job/inventory.json --standard standard.json --out review-job
python scripts/run.py convert --source input.dwg --inventory scan-job/inventory.json --standard standard.json --mapping approved-mapping.json --out verified-output
```

請換成實際輸入路徑；`approved-mapping.json` 必須是人工完成的決策，輸出資料夾必須尚未存在。完整契約見[資料契約](src/dwg-layer-standardizer/references/CONTRACTS.md)。

## 設定與資料

| 環境變數 | 用途 |
| --- | --- |
| `CADLAYER_STANDARD` | 指定圖層標準 JSON |
| `OPENAI_API_KEY` | 啟用可選的模型語意建議 |
| `CADLAYER_AI_MODEL` | 指定模型；目前程式預設為 `gpt-5-mini` |
| `CADLAYER_AI_BASE_URL` | 覆寫模型 API base URL |

未設定 `CADLAYER_STANDARD` 時，工作台載入 `DEFAULT_STANDARD.json`。若要在快速開始環境改用預設標準，將環境變數路徑的 `DEMO_STANDARD.json` 改成 `DEFAULT_STANDARD.json`，然後重新啟動服務。範例截圖與驗證使用 DEMO 標準；預設標準中的非連續線型須已存在於待處理圖面，缺少時會停止轉換。

標準格式為 `cad-layer-standard/v1`。ACI 範圍為 1–255，線寬單位為 1/100 mm；目標線型需存在於來源圖面。

模型功能會傳送標準及有界的圖層證據，包括可能含有專案資訊的文字、圖塊與屬性樣本，不傳送原始 DWG。啟用前請確認這些資料可提供給設定的 API 服務。API 金鑰請透過環境變數設定。

草稿儲存於目前瀏覽器及網址，不會跨裝置同步。重要決策請匯出映射留存。

## 限制與保護

- 保留來源檔，以 SHA-256 核對來源完整性；不覆寫來源或既有輸出目錄。
- `0`、`DEFPOINTS` 與 XREF dependent layers 只能保留。
- 不自動分解圖塊，不強制將物件 override 改為 ByLayer。
- DWG 可處理可編輯圖塊定義；DXF writer 不修改圖塊定義。
- DWG 驗證涵蓋物件、非圖層簽章及目標層屬性。AutoCAD 再生的 `*D` 尺寸快取採獨立驗證規則，詳見[原生 adapter](src/dwg-layer-standardizer/integrations/autocad-2026-native/README.md)。
- 映射不完整、來源變更、線型缺少、CAD 執行錯誤或回讀不符會停止；未通過驗證的 DWG 輸出會移除。
- 本工具處理圖層標準化，輸出仍需依專案要求進行 CAD 專業檢查。

## 開發與文件

```text
src/dwg-layer-standardizer/
├── cadlayer/       Python API、契約、建議與 CAD 路由
├── ui/             本機 Web 工作台
├── scripts/        CLI 與已安裝介面同步
├── integrations/   AutoCAD 2026 原生整合
├── standards/      示範標準
├── references/     資料契約
└── examples/       DXF 示範
 tools/             測試與維護工具
```

- [完整操作手冊](src/dwg-layer-standardizer/README.zh-TW.md)
- [原生 DWG adapter](src/dwg-layer-standardizer/integrations/autocad-2026-native/README.md)
- [貢獻指南](CONTRIBUTING.md)
- [發布文案與準備事項](docs/GITHUB_RELEASE.md)

已安裝 skill 的介面同步只取 Git HEAD 已提交內容；設定與邊界請見操作手冊。

## 授權

本專案採用 [MIT License](LICENSE)。AutoCAD 為 Autodesk 產品，使用者需自行具備適用的安裝與授權。

## 參考

README 的導覽、功能表、快速開始與流程圖編排參考 [Awesome README](https://github.com/matiassingers/awesome-readme) 收錄的呈現方式。
