# 工作紀錄：InfoGraphic2AIGCdirection

日期：2026-06-16

---

## 2026-06-16：移除關鍵卡片

今天把 `InfoGraphic2AIGCdirection` 版本中的「關鍵卡片」效果回退掉，並重新輸出影片。

變更內容：

- `skill-pptx-to-animated-video/scripts/render_final_video.py`：跳過 `key_point_card` 層，不再在成片上疊加 keyword chips
- `task=InfoGraphic2AIGCdirection/hyperframes/animation.js`：移除預覽頁的 keyword chips 生成邏輯
- `task=InfoGraphic2AIGCdirection/hyperframes/index.html`、`styles.css`：刪除對應的 `#keywords` / `.kw` 結構與樣式
- 重新渲染 `final_video_with_voiceover.mp4` 與 `final_video_with_voiceover_and_subtitles.mp4`

驗證：

- `python -m py_compile skill-pptx-to-animated-video/scripts/render_final_video.py`
- `node --check task=InfoGraphic2AIGCdirection/hyperframes/animation.js`

---

## 2026-06-16：Pipeline UI 下一步計畫

目標：在目前 `InfoGraphic2AIGCdirection` 已經能穩定輸出影片的基礎上，加一個互動式 UI，讓每張投影片的處理細節可以被檢視，之後再逐步加入安全的單張投影片調整功能。

判斷：`method_10_plan.md` 的 backend pipeline 架構有參考價值，但目前專案的實際產出流程更穩定，所以 UI 應該先包住現有流程，而不是重寫成新的 Method 10 pipeline。

Phase 1：Read-only UI

- 建立 `task=InfoGraphic2AIGCdirection/pipeline-ui/`
- 先做靜態瀏覽器 UI，不需要 server
- 讀取 `hyperframes/project.json` 顯示 slide list
- 讀取 `output/slide_xx/metadata.json` 顯示每張 slide 的 layers
- 顯示 `original.png`、`background.png`、`work_preview/slide_xx_layer_gallery.jpg`
- 顯示 `narration/narration_timing.json` 裡的 narration script、cue timing、每張 slide 的 audio file
- 目標是把目前 pipeline 的中間產物全部看清楚，不先改資料

Phase 2：Editable UI

- 加入 layer visibility toggle
- 可以調整每個 layer 的 `start`、`duration`、animation type
- 可以編輯 narration / caption text
- 可以針對單張 slide 重新 render
- 編輯內容先寫到新的 `pipeline_state.json`
- 不直接覆寫 `metadata.json` 或 `project.json`，避免破壞穩定輸出
- render script 之後再合併 generated metadata + `pipeline_state.json` overrides

建議第一步：先做 read-only `pipeline-ui/index.html`、`pipeline-ui/styles.css`、`pipeline-ui/app.js`，包含 slide navigation、preview canvas、layer list、narration/timing panel。完成後再決定哪些欄位最值得開放編輯。

---

## 2026-06-16：Phase I — 建立 Read-only Pipeline UI [COMPLETED]

已建立 `task=InfoGraphic2AIGCdirection/pipeline-ui/` 第一版，只做檢視，不寫回任何 pipeline 資料。

新增檔案：

- `pipeline-ui/index.html`
- `pipeline-ui/styles.css`
- `pipeline-ui/app.js`

目前功能：

- 從 `hyperframes/project.json` 載入 slide list
- 從 `output/slide_xx/metadata.json` 載入每張 slide 的 layer 資訊
- 從 `narration/narration_timing.json` 顯示 narration、cue timing、audio
- 中央 preview 可切換 composite、original、background、debug、layer gallery
- 右側 inspector 顯示 slide stats、narration audio、layer list
- 下方 timeline 顯示 layer start/duration 與 cue marker
- 保留 `Show skipped card layers` toggle，用來檢查目前 render 已跳過的 `key_point_card` layers

使用方式：從 `task=InfoGraphic2AIGCdirection` 啟動本機 server，再開 `/pipeline-ui/`。

```bash
python -m http.server 8000
```

驗證：`node --check task=InfoGraphic2AIGCdirection/pipeline-ui/app.js`

---

## 2026-06-16：Phase II — Editable Pipeline UI [IN PROGRESS]

在只讀 UI 基礎上加入編輯功能。

變更內容：

- `pipeline-ui/app.js`：大幅重寫，加入 `state.overrides` 管理編輯狀態
  - 旁白改為 `<textarea>`，即時編輯
  - layer 的 start／duration／animation 改為 `<input>` / `<select>`，即時編輯
  - timeline 即時反映修改後的 start／duration
  - 編輯狀態以 `dirty` flag 追蹤
  - Save overrides 按鈕：下載 `pipeline_state.json` 到本機
  - 載入時嘗試讀取 `pipeline_state.json`，合併 override
- `pipeline-ui/index.html`：加入 Save overrides 按鈕
- `pipeline-ui/styles.css`：加入 textarea、input、select、save-btn 樣式
- `pipeline-ui/app.js`：語法檢查通過

使用方式：編輯欄位 → 按 Save overrides → 下載 `pipeline_state.json` → 放到 task 根目錄 → 重新整理頁面即套用

---

## 2026-06-16：Task 下拉選單 — 適用所有 task=*

把 Pipeline UI 從僅限 InfoGraphic 改為通用 task selector。

變更內容：

- `task-index.json`：新增 root 層 task 清單，列出目前所有 task 目錄
- `pipeline-ui/app.js`：改為從 `task-index.json` 載入 task 列表，下拉切換 task 時重新載入 slides/layers/timing
- `pipeline-ui/index.html`、`styles.css`：加入 topbar 下拉選單樣式

 使用方式：

```bash
python -m http.server 8000
# 開 http://localhost:8000/task=InfoGraphic2AIGCdirection/pipeline-ui/
# 從 topbar dropdown 切換到其他 task
```

驗證：`node --check task=InfoGraphic2AIGCdirection/pipeline-ui/app.js`

---

## 2026-06-16：Phase II 補完 — Pipeline Server + 中英混用旁白

變更內容：

- `pipeline_server.py`：新增 pipeline action server
  - POST `/apply` 接收 `pipeline_state.json` overrides
  - 自動更新 `narration_script.md` → 重跑 TTS → patch metadata → 重建 timeline
  - CORS 支援，預設埠 8001
- `pipeline-ui/app.js`：加入 Apply to pipeline 按鈕
  - 將 `state.overrides` POST 到 pipeline server
  - 顯示 server 回傳的 logs
- `pipeline-ui/index.html`、`styles.css`：加入 apply-btn、log panel
- `narration/narration_script.md`：改為中英混用版
- 重跑 TTS（`zh-TW-YunJheNeural` 男聲）+ 重建 timeline

使用方式：

```bash
# 終端 1：靜態檔案伺服器
cd /project/root
python -m http.server 8000

# 終端 2：Pipeline action server
cd task=InfoGraphic2AIGCdirection
python pipeline_server.py 8001

# 瀏覽器
# http://localhost:8000/task=InfoGraphic2AIGCdirection/pipeline-ui/
# 編輯 → Save overrides → Apply to pipeline
```

---

## 2026-06-16：Session Summary — 完整工作記錄

### 環境與技能
- 安裝 `npx skills add heygen-com/hyperframes`（16 個 skills）
- 安裝 Python 依賴（opencv-python, edge-tts, pymupdf, pillow）
- 確認 ffmpeg 可用

### 旁白與 TTS
- 旁白稿從純英文改為**中英混用**版（技術關鍵字保留英文，解說用繁體中文）
- TTS 語音從 `en-US-GuyNeural` 改為 `zh-TW-YunJheNeural`（台灣男聲，支援中英混讀）
- 語速從 +0% 調整為 -10%（自然教學語速）

### Slide 01 切圖修正
- 將原始 title block（含 "Designing the Future of Work" + "From Narrow AI to General AI"）手動拆成兩個獨立 PNG 區塊
- title_main.png：上半部 "Designing the Future of Work"
- title_sub.png：下半部 "From Narrow AI to General AI"
- 動畫順序：title fade-in-down → subtitle fade-in-up → 圖表群 zoom-in

### Pipeline UI 演進
1. **Phase I**：只讀檢視器 — slide list、composite preview、layer list、narration、timeline
2. **中間進化**：task 下拉選單（支援所有 task=*）、主題切換（Dark/Light/Slate/Warm）
3. **Phase II**：可編輯 UI — narration 可編輯、layer start/duration/animation 可調、Save overrides 下載 pipeline_state.json、Apply to pipeline 按鈕
4. **Pipeline Server**：`pipeline_server.py` 合併靜態檔案伺服 + API（POST /apply、POST /suggest）、自動處理調整備註（偵測關鍵字如「慢一點」→ 自動重跑 TTS）
5. **最終版**：簡化為 slide preview + play button + slide 下拉選單（preview-ui tag）

### Git 標籤
- `editable-narration-block` — Phase II 可編輯旁白完成點
- `preview-ui` — 最終簡化版 slide 預覽
- `preview-ok-v2` — 只讀檢視器正式版（layer 編號 + highlight、adjustment notes、MP4 export）

### 目前使用方式
```bash
cd task=InfoGraphic2AIGCdirection
python pipeline_server.py 8000
# 打開 http://localhost:8000/task=InfoGraphic2AIGCdirection/pipeline-ui/
```

---

## 2026-06-16：preview-ok-v2 — 只讀檢視器正式版

### 功能
- 左欄：所有 slides 列表
- 中央：preview（Composite / Original / Background / Debug / Gallery）
  - ▶ Play slide：動畫播放 + 字幕 + 語音同步
  - Open in HyperFrames：開完整 deck 預覽
  - MP4 export：選取 slides 範圍 → 伺服器渲染成 MP4
- 右欄：slide 數據、旁白文字 + 語音播放、Adjustment notes、layers 列表
  - Layer 列表有編號（1, 2, 3...）
  - 點擊 layer → preview 上對應 component 亮起 highlight
- 頂部：Task 切換（所有 task=*）、Theme 切換（Dark/Light/Slate/Warm）
- 所有修改透過 agent 對話進行，UI 僅做 review

### 已知限制
- Adjustment notes 的 auto-processor 只支援簡單關鍵字（慢一點、快一點、換聲音等），複雜的切圖/動畫調整仍需透過 agent 對話執行
- Slide 01 的 title 拆分使用手動 PNG 裁切方式，未整合進 segment_elements.py 的 OVERRIDES 機制
- 部分 slides 的切圖仍可能不完全（text block 合併、collage cluster 等）
