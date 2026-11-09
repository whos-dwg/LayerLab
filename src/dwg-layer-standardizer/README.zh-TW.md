# LayerLab v1.0.0-preview.4 操作手冊

以原生 DWG 為主的圖層盤點、人工審核與標準化 skill。DWG 由 AutoCAD 2026 原生處理；不會先轉成 DXF，也不會覆寫來源檔。DXF 僅保留為相容流程。

## 啟動工作台

公開原始碼包請先依[首頁快速開始](../../README.md#快速開始)建立根目錄虛擬環境、安裝相依套件並以 uvicorn 啟動。目前公開下載入口為 [v1.0.0-preview.4 Release](https://github.com/whos-dwg/LayerLab/releases/tag/v1.0.0-preview.4)。原生 DWG 需要 AutoCAD 2026 Update 1.2 以上與 .NET 10 SDK；DXF 示範不需要這兩項。

已安裝且完成同步設定的 skill 可雙擊 `start-layerlab.bat`，或在 `src/dwg-layer-standardizer` 目錄、使用已安裝相依套件的 Python 執行：

```powershell
python scripts/run.py serve --port 8765 --open-browser
```

正式工作台可用 `CADLAYER_STANDARD` 指定已核准的標準 JSON；未設定時載入內附匿名化的 DEFAULT_STANDARD.json（48 層）；示範操作請明確指定 DEMO_STANDARD.json。

預設標準在工作台顯示為 `default`。使用「切換標準」匯入其他標準後，以匯入檔案名稱顯示；復原及恢復草稿也會保留對應名稱。顯示名稱與原始標準內容分開，不會因改名改動圖層定義或草稿核對指紋。

### 已安裝 skill 的自動同步

專案目錄可用本機 `.layerlab-sync.json` 指定自己的 `standard_path`，並以 `sync_target` 指向原有安裝副本的 UI 同步位置。這讓專案採用新標準，同時保留安裝副本的自訂 CAD 引擎；本機設定不納入 Git。

此功能需要 Git checkout。本版原始碼包已包含 `tools/test-workbench-core.cjs` 及 `tools/git-hooks/post-commit`、`post-merge`、`post-checkout`；ZIP 本身不帶 `.git`，直接使用 ZIP 時請依上方獨立 uvicorn 流程啟動。

本機安裝可用以下命令連結此 Git 專案，並指定使用者核准的正式標準：

```powershell
python scripts/sync_ui.py install --repo "C:\Projects\LayerLab" --target "$env:USERPROFILE\.codex\skills\dwg-layer-standardizer" --standard "正式標準.json"
```

設定完成後，commit、merge／pull、切換分支或版本會觸發 Git hook。`scripts/run.py serve` 和雙擊啟動器在啟動前也會補做檢查。同步只取 Git HEAD 已提交的介面與同步檢查程式；未提交的試作不會被安裝。更新先做 JavaScript 語法檢查及已提交的核心測試，再備份舊檔、逐檔核對雜湊，最後發布入口 HTML。HTML 的資產版本會使用 commit 編號，避免舊快取。同步失敗會保留／復原原版並回報錯誤；不會自動推送 Git。

同步包含介面、`cadlayer/app.py` API 入口與同步檢查程式；不包含其他 CAD 引擎模組、`standards/`、`.venv/` 或工作資料。安裝時會替啟動器加上同步檢查。舊檔保存在已安裝目錄的 `.layerlab-ui-backups/`，每次附有 manifest。既有 Git hooks 不會被自動取代。

已安裝目錄的 `.layerlab-sync.json` 保存來源專案與正式標準路徑。正式標準直接讀取該路徑，介面同步不複製或覆寫它；找不到設定的正式標準時停止啟動，不會退回示範標準。明確設定的 `CADLAYER_STANDARD` 仍可覆寫預設。尚未連結安裝副本的獨立專案維持原本的示範模式。

手動補做同步：`python scripts/sync_ui.py sync --repo "C:\Projects\LayerLab"`。
同步流程測試：在專案根目錄執行 `python -m unittest discover -s tools -p test_ui_sync.py -v`。

Git hooks 優先使用專案虛擬環境的 Python。同步工具會檢查可執行的 Git；Windows 啟動入口失效時可使用同套 Git 或 Codex 執行環境內的實際執行檔，不修改系統 PATH。

## 名稱與內容判讀

完整且明確的中文名稱可直接提出建議，包括 `窗` → `A-wind`、`家具` → `A-furn`、`內牆` → `A-wall`；空白圖層也適用。若空白且名稱無法辨識，並且標準含 `0`，才預設建議至 `0`；有物件的未知圖層繼續判讀內容，不因無法看懂名稱而歸零。

正式標準 1.2.0 與匿名化預設標準共 48 層，新增系統目標 `0`。它可接收其他圖層，但來源 `0`、Defpoints 與 XREF 仍維持保護；映入 `0` 時保留原圖面 0 層的既有屬性。新增建議仍須確認才會執行另存。

圖層名稱以不分大小寫的完整詞段對應標準名稱或別名後綴，例如 `A-WALL-BW`、`A-WALL-DW` → `A-wall`；未帶明確專業前綴的 `DOOR`、`HATCH` 分別優先對應 `A-door`、`A-hatch`。明確的 `LA-`、`RC-` 等前綴仍保留所屬範圍，多段後綴以較完整的用途為準，不以任意子字串或拼字近似強行指定。

依使用者指定規則，`rc`、`RC牆` → `A-wall`，`SITE`、`挑空`、`TEXT` → `A-text`。目標名稱及大小寫以當前核准標準為準；指定目標不存在時保持待判讀。

名稱無直接對應時，綜合物件組成、具名圖塊與其占比、幾何抽樣判讀。少數具名圖塊、匿名圖塊、一般線段或狹長矩形不能單獨代表整層用途；內容衝突或證據不足時保留待判讀。文字與尺寸物件分別尋找一般文字層與尺寸層，不因類別同為 annotation 而混同。

「建議工具 → 重新判讀」可更新既有盤點的規則建議，保留人工決策、暫定映射及勾選範圍，並可復原。規則建議與模型建議均不代表核准，也不會因此寫入圖面。命令列 `scan`、`suggest` 與工作台沿用相同規則；模型只處理尚無明確名稱對應的圖層，幾何證據保留比例、圓心、圓弧角度及抽樣截斷資訊。

## 模型語意判定

設定 `OPENAI_API_KEY` 後，工作台會啟用「模型判定」。可選用 `CADLAYER_AI_MODEL` 指定模型（預設 `gpt-5-mini`），並以 `CADLAYER_AI_BASE_URL` 覆寫 API base URL。只會傳送有界的圖層盤點證據與正式標準，不會傳送原始 DWG。每次結果會保存獨立稽核 JSON；模型建議仍須由使用者明確採用。

介面可匯入 `.dwg`、`.dxf` 或 `cad-layer-inventory/v1` JSON。初次顯示的建議不是核准結果；所有非保護來源層必須由使用者明確選擇「映射」或「保留」。

工作台採用映射編排台：左側是來源圖層，右側是可收合的標準目標，下方保留目前來源的幾何、證據與決策。兩側可獨立搜尋，目標另可按標準分類或「只看有來源」篩選。點選來源名稱只改變檢視；勾選方框決定批次操作範圍。

來源每頁顯示 30 層，全部標準目標集中於同一清單，不再分頁；標題顯示標準版本及完整／篩選後的目標數。可獨立搜尋、按分類或「只看有來源」篩選，並一鍵清除目標篩選。一次展開一個目標，其來源明細每頁顯示 6 層。勾選跨頁、跨篩選保留，並顯示目前頁面以外的勾選數。「全選此頁」、「選取篩選結果 N 層」及「選取此目標全部 N 層」的範圍分別標示。

初次載入、匯入圖面、切換標準及恢復草稿後，準備指定的目標保持未選；點選標準列或拖放才選定目標。「來源的建議／暫定／已確認映射」與「準備指定的目標」分別標示。有勾選時突出批次操作並隱藏單層決策；沒有勾選時顯示目前來源的操作。主要物件類型與建議理由常駐，詳細 CAD 屬性與完整證據仍可展開。

拖曳來源到目標，或按「指定勾選的 N 層」／「指定目前圖層」，只會建立暫定目標。必須另外按「確認勾選映射」／「確認目前圖層」才計入人工審核。批次確認保留每層各自的目標；任一勾選來源尚無有效目標時，整批均不核准。重新指定已審核的圖層會撤回該層核准。未確認的匯出草稿以 `proposed_target` 保存暫定目標，`decision` 仍為 `pending`，不可執行轉換。

幾何預覽支援符合選取、全圖脈絡、縮放、拖曳平移與大型檢視。游標在下方縮圖停留約 140ms，會暫時顯示約三倍寬的預覽，移開或按 Escape 收回；鍵盤 Tab 聚焦也能開啟。此功能保留原本的視野與 zoom，不擠動清單。背景幾何有全域樣本上限。

審核草稿按工作自動保存於目前網址的瀏覽器儲存空間，包含人工決策、暫定目標、建議、目前來源及勾選範圍，不複製原始圖面或完整幾何。重新開啟後可選擇保存草稿，按「恢復上次審核」才帶入進度。恢復前重新讀取伺服器原盤點，並核對盤點及整份標準的 SHA-256；標準或盤點變更、原工作不存在、草稿格式損壞時，拒絕沿用核准，原保存草稿保留。暫定目標恢復後仍是待審，不會執行轉換。

儲存失敗時顯示「未保存」並提示使用匯出映射，離開未保存進度時會提醒。同一工作被另一分頁更新時，本頁暫停自動保存，須明確選擇接管才覆寫。草稿屬於目前瀏覽器及網址；清除網站資料、改用另一網址或裝置不會自動帶入，重要進度仍可匯出映射留存。

「建議工具」位於上方可展開的工作區資訊內。「採用所有圖層的建議」包含目前搜尋、篩選及分頁以外的圖層，會先顯示影響範圍；手動指定的暫定目標與既有人工決策保持不變。映射變更與標準切換提供上一步復原。切換標準時，可選擇保留相容決策；只有保留原圖層或目標 CAD 屬性未變更的決策可沿用。另存期間會鎖定資料變更，失敗原因保留於畫面，不會自動重試寫入。

以下測試腳本已納入本版原始碼包。前端核心規則的回歸測試可在專案根目錄執行：`node --test tools/test-workbench-core.cjs`。涵蓋人工審核與系統保護的區分、篩選、全選狀態、標準切換、幾何判斷、暫定與核准分離、批次確認的完整性，以及 300 個來源與 160 個目標的批次操作。

完整前端互動回歸：先以 `npm install --prefix temp/layerlab-dom-tests --no-save --ignore-scripts jsdom@26.1.0` 安裝隔離測試依賴，再執行 `node --test tools/test-workbench-ui.cjs`。此測試使用完全合成的測試標準及模擬 API，涵蓋完整目標清單、跨頁批次、重新開啟恢復、來源／標準不相符、儲存失敗與分頁衝突；不啟動 CAD。

## DWG 原生流程

`讀取 DWG → 原生內容盤點 → 人工核准 mapping → 第一個 AutoCAD 程序寫入副本 → 程序退出 → 第二個程序重開並清理來源層 → 程序退出 → 第三個程序重開驗證 → 報告`

驗證項目包括：

- 原始 DWG SHA-256 前後一致；
- 物件 handle 與數量一致；
- 忽略 layer 欄位後的物件類型、幾何範圍、文字／圖塊與 override 簽章一致；
- 每個已映射物件位於核准的目標層；
- 目標層的 ACI、線型、線寬、出圖屬性符合標準；
- 模型空間、配置與可編輯圖塊定義都納入；XREF 內容保持不動；
- 映射到不同目標、且不再被物件或其他映射引用的來源圖層會被移除；同名屬性更新、受保護層及物件層級保留例外不刪除；
- 任一門檻失敗即刪除未驗證的 DWG 輸出。

完成畫面以通過 Save/Reopen 的標準化 DWG 為主要成果，另提供「匯出 DWS」選配按鈕。DWS 保存本次已核准的來源層→標準層映射及使用到的目標圖層屬性，供後續在 AutoCAD `LAYTRANS → Load` 重用；它不取代 DWG。產生 DWS 時必須有一個已啟動、目前沒有執行中命令的 AutoCAD 2026 桌面工作階段。系統只會暫開可丟棄的來源副本，實際載入並套用 DWS、儲存副本，再由新的 Core Console 程序有界清理仍存在的核准來源層，最後以另一個程序重開核對；原本的使用者文件會重新啟用且不會被儲存或關閉。

DWS 成功會產生 `*_layer_mapping.dws` 與 `dws-validation.json`；報告必須包含 `actual_autocad_load: true`，並確認映射來源圖層已在翻譯後移除。若桌面 AutoCAD 忙碌、DWS 載入／套用／重開驗證失敗，未驗證的 DWS 會被移除，畫面顯示「DWG 已完成、DWS 未產生」，已通過的主要 DWG 維持成功。

統一命令列入口（從 `src/dwg-layer-standardizer` 執行；根目錄 venv 使用者請以 `..\..\.venv\Scripts\python.exe` 取代 `python`）：

```powershell
python scripts/run.py health
python scripts/run.py scan --source input.dwg --standard standard.json --out scan-job
python scripts/run.py suggest --inventory scan-job/inventory.json --standard standard.json --out review-job
python scripts/run.py convert --source input.dwg --inventory scan-job/inventory.json --standard standard.json --mapping approved-mapping.json --out verified-output
```

底層 AutoCAD adapter 用法見 `integrations/autocad-2026-native/README.md`。

## DXF 相容流程

DXF 仍使用 `ezdxf` 執行非破壞性副本轉換與回讀驗證。DXF writer 不修改圖塊定義。

## 圖層標準

正式標準使用 `cad-layer-standard/v1` JSON。內附 `standards/DEMO_STANDARD.json` 只供示範，不代表使用者的公司標準。ACI 為 1–255；lineweight 單位為 1/100 mm；目標線型必須存在於來源 DWG/DXF，否則 fail closed。

## 安全邊界

- `0`、`DEFPOINTS`、XREF dependent layer 只能保留。
- 不自動 explode block。
- 不強制把物件 override 改成 ByLayer。
- 不覆寫來源檔或既有輸出資料夾。
- 不完整 mapping、hash 漂移、缺少線型、AutoCAD 錯誤或回讀不一致都會停止寫入。

DWS 本身保存圖層映射，不包含清理程式。手動在其他圖面重用時，AutoCAD 可能保留來源空層；主要標準化 DWG 仍是完整驗證成果。自動 DWS 驗收會在可丟棄副本補做有界清理，未通過完整回讀仍不提供 DWS。
