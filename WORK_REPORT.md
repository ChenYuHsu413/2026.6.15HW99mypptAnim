# Work Report — Image-PPTX → Animated Narrated Video

期間：2026-06-12 ~ 2026-06-13（三個 Claude Code session）
專案：`HW99/`（NotebookLM 圖片型 PPTX）、`HW99/writing-os/`（The A-Z Writing OS PDF）
最終產出：
- 可重用的 Claude Code skill：`.claude/skills/skill-pptx-to-animated-video/`
- 兩支動畫旁白 MP4（無字幕版 / 燒錄字幕版，writing-os 子專案）
- 完整 segmentation + 旁白 + 字幕 + HyperFrames 預覽 pipeline

---

## 1. 起點與目標

把 NotebookLM 出的「圖片型 PPTX/PDF」（每頁是一張平面圖、沒有可編輯元件）變成 1920x1080 / 30fps 動畫 MP4：
- 把每頁切成可動畫化的 element layers（卡片、箭頭、icon、illustration、highlight、annotation 等）
- 用旁白時間軸反向安排各 layer 進場時間
- 燒錄繁中女聲 TTS 旁白與字幕

第一版 segmentation 太粗（只切大區塊）。整個專案的價值在「每一次人工 review 之後把學到的規則寫回演算法」——讓 skill 在下個 deck 仍然能用。

---

## 2. Session 1 — 切圖規則奠基（HW99/，NotebookLM deck）

### 主要進展
- 建立 PDF→PNG→segmentation→narration→timeline→render 全 pipeline。
- segmentation 從「大區塊偵測」改成兩階段：偵測卡片（hole+border ring）→ 擦除卡片後切剩餘 ink。
- 紅圈 highlight、虛線箭頭、紅色圖表註記三類特殊規則進演算法。
- 文字行合併：詞距與字高耦合，欄位走廊（cross-column corridor）vetoes 防止跨欄串接。
- 13 頁 layers 全部通過「重組原圖 0px 差異」驗證。

### 人工 review 改動
- slide 11：`arrow_01 + table_01` 視為整體；highlight 群組概念引入。
- slide 01：閱讀順序 5/6/7 修正為符合人類由上而下、左到右。
- slide 04：左右箭頭切法一致化。
- slide 05、15、18：小面積 fragment 折回卡片，不獨立成 layer。

### Skill 化
- 將整套流程包成 `.claude/skills/skill-pptx-to-animated-video/`，描述 + 7 個 workflow 步驟 + segmentation 品質規則。
- 推到 GitHub `ChenYuHsu413/HW99`。

---

## 3. Session 2 — Skill 首次跨專案使用（writing-os）

### 主要進展
- 在 `writing-os/` 子資料夾使用 `/skill-pptx-to-animated-video` skill 處理「The A-Z Writing OS.pdf」（13 頁）。
- 自動產生：13 頁切圖、繁中旁白稿、TTS MP3、subtitle、HyperFrames 預覽、兩支 MP4。

### 演算法回授
- chevron-letter 字體大、空心（hollow），與普通文字行的合併規則衝突 → 加入「letter 字高 ≥ 130 且密度 < 0.22 不合併」例外。
- 連結器網（connector skeleton）被偵測成單一巨型 contour → 用 morphology close 把網絡內的高密度節點（字、卡片）救回成 piece，網本身留在背景。
- 殘留長細條（divider line 餘骸）排除。
- 0.35 size cap 與 0.9 width/height cap 用來區別「真正的卡片」與「裝飾外框」。

---

## 4. Session 3 — 把 skill 練成可重用工具（今天）

User 一開始就強調：「I want to develop a skill, not just only for these slides」——所以這 session 的核心是把每次人工 review 學到的東西**內化進演算法**，不是堆疊 per-slide override。

### 4.1 Slide 2 切圖再深化
| 問題 | 解決 |
|---|---|
| Paper pile + REJECTED 應該是一個元素 | 預設改為合併 stamp 進入底層 illustration（移除 `exclude_red: True`）。 |
| 右側 DIAGNOSTIC TERMINAL 區塊應該是一個元素 | OVERRIDE 提供合併框；之後在演算法層發現可由 `detect_cards` 自動偵測。 |
| 左側內容要先於右側 | OVERRIDE 加 `order` regions；之後因兩個 panel 都被 cards 偵測，row-cluster sort 自動正確排序。 |
| 問題: 為什麼…? 要先於右側 diagnostic | OVERRIDE 加 order 區隔。 |
| 元素 1（[TRADITIONAL header）不必要 | 後續演算法升級後 left panel 被當作整體卡片，TRADITIONAL 自然被包進去。 |
| 左側其實是一塊「連續元素」 | 抬高 `detect_cards` 面積上限 0.35→0.48，讓 ring=1.0 的半版面 panel 可成為卡片。 |
| Footer banner 被吞進 right card 撐大成全螢幕 | 修正 trim/absorb 規則：絕對 cut > 14 不夠，還要 `cut > 0.20 * perpendicular_dim`。 |

### 4.2 Slide 4 切圖修正
- Column C 「Cure:」label 因過短被遺漏 → 新增 `tight` merge spec 拉回。

### 4.3 三大演算法升級（內化進 `segment_elements.py`）

**A. 半版面 panel-card（提升 `detect_cards` 面積上限 0.35 → 0.48）**

  Half-slide 邊框完整的區域（ring fraction = 1.0、面積 35–48%）視為 single panel-card。0.9 width/height cap 仍擋住整版 chrome。slide 2 左右 panel 雙雙自動偵測成功。

**B. `collage_cluster` 通用 pass**

  在 word/line merge 之後跑：找 3+ 個 piece，gap ≤ 80px，axis overlap，並通過六道閘：
  - 連通分量大小 ≥ 3
  - bbox ≤ 30% 投影面積
  - piece-bbox 密度 ≥ 30%
  - 長寬比 ≤ 4
  - **raw ink ratio ≥ 0.17**（slide 2 paper pile 0.19 過、slide 4 A/B chevron grid 0.14 退）
  - 無 cross-column corridor

  Paper pile 從此自動合併，不靠 OVERRIDE。

**C. trim/absorb fraction rule**

  原本 `cut > 14` 直接 absorb 會讓 footer banner 被卡片吞掉、bbox 暴漲至全螢幕。改成同時要求 `cut > 0.20 * perpendicular_dim`：15px 切口在 124px 高的 footer 上只是 12%，應 trim 不應 absorb。

### 4.4 影片產生

- TTS 速率從 `-8%`（教學語速）→ `+38%`（≈1.5× 之前速度），重生 13 段 MP3。
- 解決 ffprobe 找不到的問題：建立 `writing-os/node_modules` → `../node_modules` junction。
- 重新 segment、build timeline、render：83 layers、4:02 min、20 MB 無字幕版 + 23 MB 字幕版。

### 4.5 字幕優化

**問題：** 每頁旁白 100–160 字塞成一個 SRT cue，跨 12–23 秒；FontSize=11 下換行 4–5 行，遮住投影片底部內容。

**解法（兩階段）：**
1. **Chunking（`build_timeline.py`）：** 新增 `chunk_narration()`，先以 `。！？` 切句，過長者再用 `，：；、` 切子句，每 chunk ≤ 32 個 CJK 字，按字數比例分配時間。
2. **字幕樣式（`render_final_video.py`）：** 半透明黑底 box（`BorderStyle=3` + `BackColour=&H66000000`），白字（`PrimaryColour=&H00FFFFFF`——注意 libass alpha 反過來，`FF=透明`、`00=不透明`，第一次寫成 `&HFFFFFFFF` 結果文字消失）。

**再深化：letterbox**

  使用者反映「有些 slide 底部本來就有內容」。最終 Filter chain：
  ```
  scale=1920:960,pad=1920:1080:0:0:color=0x101010,subtitles=...
  ```
  畫面下方 120px 變成字幕專用暗條；無論 deck 怎麼設計，字幕永遠不會碰到 slide 內容。輕微 11% 垂直壓縮，肉眼幾乎察覺不出。

  「只重燒字幕、不重 render」用獨立 ffmpeg 指令完成，省下 ~10 分鐘。

### 4.6 Skill 同步更新

`.claude/skills/skill-pptx-to-animated-video/` 收到本日所有改動：

- **`SKILL.md`** 新增 / 改寫：
  - Step 3 TTS：`+38%` 1.5× 加速指引、`node_modules` junction 解法。
  - Panel-card 規則（面積上限 0.48）。
  - `collage_cluster` 規則（6 道閘的閾值來源）。
  - Stamp/highlight 預設併入底層元素（`exclude_red` 改成 opt-in）。
  - trim/absorb 必須是 fraction-of-piece（避免 footer 吞卡片）。
  - 字幕分塊（`chunk_narration`）章節。
  - 字幕燒錄 letterbox + ASS style + libass alpha 反向警告。
  - 「Per-slide OVERRIDES vs algorithm changes」：算法優先、override 最後手段。

- **`scripts/segment_elements.py`** baseline 版：`OVERRIDES = {}`，但保留所有 flag 的 inline 註解。三大演算法升級全部內建。
- **`scripts/build_timeline.py`**：`chunk_narration` 函式 + `SUB_CHUNK_MAX = 32` 常數 + 比例分配時間。
- **`scripts/render_final_video.py`**：letterbox + 修正後的 ASS style。

---

## 5. 量化成果

| 指標 | Day 1 結束 | Day 3 結束 |
|---|---|---|
| Skill 是否可跨 deck 重用 | 初版 | 演算法成熟、覆蓋多 case |
| Slide 2 layer 數 | 6（碎） | 4（左 panel / REJECTED annotation / 右 panel / 底部 footer） |
| Slide 4 layer 數 | 14（缺 C 欄 label） | 14（完整） |
| Per-slide OVERRIDE entries（writing-os） | 7 條 | 4 條（slide 2 整條移除） |
| 字幕單 cue 字數上限 | 全頁旁白（100–160 字） | 32 字 |
| 字幕是否會遮 slide 內容 | 會 | 不會（letterbox 專用暗條） |
| 旁白語速 | -8%（慢） | +38%（≈1.5× 快） |

---

## 6. 未完成 / 後續可優化

- 字幕同步精度：目前按字數比例分配 chunk 時間。若要逐字 timestamp，可改用 whisper-timestamped 或 forced-alignment。
- TTS 多語：目前 zh-TW 預設，英語 deck 要替換 voice + 詞距、字距規則。
- 黑色 letterbox 顏色：`0x101010` 與字幕 box 顏色接近一致，若 slide 主視覺也是深色可能融在一起；可改 deck-specific 背景色。
- collage 偵測：`ink_ratio ≥ 0.17` 閾值僅在 writing-os deck 校準，未來其他 deck 仍需 layer gallery review。

---

## 7. Session 4 — 架構重整：合約層 + overrides + 可視化編輯（2026-06-18）

User 想把這套東西長成一個「上傳 → 切圖 → UI 檢查/調整 → 生成影片」的系統，所以這個 session 的重點
不是切圖，而是**把資料流重整成可編輯、可替換、互不汙染的架構**。全程每一步都用「無 override 時輸出
byte 不變」當回歸測試（git diff 空），沒有重新 render（環境無 ffprobe/網路，render/TTS 在 user 機器上跑）。

### 7.0 先釐清的兩個誤會
- **切圖 ≠ HyperFrames**：切圖一直是自製 `segment_elements.py`（OpenCV）；HyperFrames 是渲染框架，
  本專案根本沒接它，最終影片是自製 numpy+ffmpeg 渲染器。
- **「只讀 PDF」不是退化**：來源是圖片型 PPTX（無可編輯元件），native PPTX 解析從未被實作，PDF 只是
  最乾淨的「投影片→圖片」中繼。

### 7.1 兩個架構決策（寫進 ANALYSIS_REPORT 第 10、11 節）
1. **渲染路線**：先用自製渲染器（已驗證），但把「渲染段」設計成讀一份**渲染器中立的合約**，替未來接
   HyperFrames 留門。draft 預覽也走同一份合約。
2. **PPTX 範圍**：範圍 2 —— UI 接受 .pptx、後端自動轉 PDF，引擎不變（原生 PPTX 快速路徑列為未來分支）。

### 7.2 實作（依序，每步 commit + push）
| 步驟 | 內容 | 驗證 |
|---|---|---|
| config 三檔 | 把寫死的 canvas/voice/caption 抽成 `config/*.json` + `config.py`（skill 預設 ⊕ task 覆蓋） | build_timeline 重生 git diff 空；ffmpeg 字幕字串逐字相同 |
| composition 合約 | `build_composition.py` → `composition.json`（canvas + caption_style + 每層穩定 id + 語意 enter）；`validate_composition.py` 驗證 | 4 task generate+validate 全 faithful（216 layers） |
| overrides 層 | `overrides.json`（keyed by slide）；`build_composition` 套用；**pipeline_server 停止覆寫 metadata/narration_script** | override 進 composition、metadata pristine；server /apply 不動生成檔 |
| overrides→TTS/timeline | `tts_edge`/`build_timeline` 改讀 effective 旁白；`media.py` 對「編輯過的頁」從當前音檔重量 duration（解耦切圖） | 無 override 時輸出 byte 不變；旁白 override 進字幕/timeline/composition |
| render adapter | `render_final_video.py` 改讀 `composition.json` | 結構等價：render 吃到的資料與舊路徑逐欄位相同 |
| draft 預覽 adapter | `hyperframes/animation.js` 改讀 `composition.json`（與成片同源，消除漂移） | node --check 過；composition 欄位齊全 |
| 共用 pipeline-ui | repo 根目錄 composition-driven 三欄 inspector，瀏覽全部 task；`pipeline_server.py`（多 task、防穿越、port 9001） | server GET/POST 全 200；/apply 寫 overrides、生成檔 pristine |
| 直接編輯控制項 | UI 內可編旁白/語音/layer(start/duration/animation)，寫結構化 overrides，套用後自動重載（所見即所得）；notes 保留當補充 | layer 編輯進 composition、metadata pristine；UTF-8 旁白編輯進 timeline/composition |

### 7.3 踩到的環境坑
- **WinError 10013 / port 8000-8099 被保留**：學校還原卡電腦擋掉這段 port 的 socket bind。改用
  9001/9000/3000/5000/5500/9090。README 與 server 預設都改成 9001。
- **ffprobe / edge-tts 不在這台**：duration refresh 與 TTS 走優雅降級（fallback baked / 記錄錯誤不致命），
  邏輯已驗證，實際音檔/時長更新需在 user 有網路+ffprobe 的機器跑。

### 7.4 成果
資料流變成乾淨三層、兩端同源：
```
generated（metadata/narration_script，唯讀）
   └─ overrides.json（編輯只動這裡）
        └─ composition.json（resolved 合約）──┬─ render_final_video → MP4
                                              └─ hyperframes draft 預覽 / pipeline-ui
```
ANALYSIS_REPORT 的 P0 全完成、P1 部分完成；新增可視化編輯 UI。詳細打勾見 ANALYSIS_REPORT 第 7、8 節。

### 7.5 後續
- per-layer `start` 真正移到 timeline 重算（目前 baked + clamp）。
- OCR + confidence（解鎖切圖修正 UI 與真 AI storyboard）。
- UI 切圖修正（合併/拆分/bbox）。
- PPTX 自動轉 PDF 的實作（範圍 2 目前只到決策）。
- 收斂各 task 內漂移的 `scripts/` 副本到單一 skill。

---

## 8. Session 5 — 上傳/Pipeline 自動化 + OCR + 切圖編輯 v1/v2（2026-06-19 ~ 06-20）

從「研究本機跑得起來嗎」到「上傳 .pptx 後可以一鍵跑全流程、UI 內可以合併/隱藏/拖框」。中間試了
cloudflared 公網分享與 Claude API 旁白潤稿，討論完都撤回（user 只要本機 + 不想處理 API key）。

### 8.1 新功能（按時間順序）
| 功能 | 內容 |
|---|---|
| **PPTX 上傳 + 自動轉 PDF** | UI Topbar 加「Upload deck」按鈕、`/ingest` 端點解 multipart、`convert_pptx_to_pdf.py` 透過 LibreOffice headless 轉檔。第一次發現 LibreOffice 未裝，winget 安裝後恢復。|
| **Pipeline 一鍵跑完** | `/pipeline` 端點順跑 render → ocr → tts → segment → timeline → composition；UI 在 pending state 顯示 ▶ Run pipeline，結果面板列每步 ✓/✗ + 耗時。動畫 "Running… 12s" 計時。|
| **Starter narration** | TTS 卡關時 result panel 出現「📝 Generate starter narration & re-run」按鈕，自動產 `narration_script.md`，body 從 `slide_ocr.json` seed（若已 OCR 完）。|
| **OCR + confidence** | `ocr_slides.py` 用 RapidOCR 3.x、`Rec.lang_type: CHINESE_CHT`；conf 從預設 simplified 的 0.83 跳到 zh-TW 的 0.96，輸出已是繁體（狀態、預測、資料、選擇）。每張 slide 寫 `slide_ocr.json`（text/confidence/lines）。|
| **Pending-state UI** | 沒 composition.json 的 task 不再讓 UI 白屏；改顯示說明 + 五步 CLI 指令 + Run pipeline 按鈕。task-index.json 上傳時自動加入、GET 時自我 prune（資料夾被刪自動清除）。|
| **切圖修正 v1：Merge + Hide + Reorder** | `overrides.py` 加 `is_hidden / merge_group_of`。`build_composition.py` 在 group 內 inherit start + animation（primary = 名字第一個）。UI 加 👁/↑/↓ 每層按鈕、Ctrl-click 多選、浮動 action bar（Merge/Hide/Show/Ungroup/Clear）、group 色彩 dot。|
| **切圖修正 v2：Bbox 拖拉** | `reextract.py` 從 `original.png − background.png` 重切透明 PNG → `output/slide_##/edits/<name>`，alpha 用 6→60 magnitude 軟閾值 feather。`/apply` 偵測 bbox 變更時自動 reextract。`build_composition.py` 偏好 edits/ 路徑當存在時。UI 在選中 layer 上畫 4 角 handle + 中間 move zone（z=10000 避免被其他 layer wrap 蓋掉）。|

### 8.2 修掉的 bug / 改善的體驗
- Stop play 不會 pause `<audio>`（既有問題） → 修了。
- Server 被瀏覽器中途 disconnect 噴 ConnectionAbortedError → 加 try/except 攔截，不再洗 log。
- 切圖編輯按鈕太小 / 版面太擠（user feedback） → la-btn 34×30/14px、layer-row-top + layer-acts 分行、meta 砍掉 x/y/w/h 改放動作列尾的小字。
- 上傳期間等待沒回饋（user feedback） → 動畫文字 + 已耗時秒數計。
- **關鍵 bug**：`apply_overrides` 的 `audio_changed` 檢查 **merged** state，導致 HW6（有 voice + 有 narration override）每按一次 eye 都觸發完整 TTS 重跑（~40s），單執行緒 server 卡死。改檢查 **incoming** payload，只在這次真的動到 voice / narration 才重跑。

### 8.3 試完撤掉的（重要：別在以後重做）
- **cloudflared 公網分享** — user 只要本機，cloudflared 也已 winget uninstall 乾淨。pipeline_server.py 的 `0.0.0.0 share` 旗標保留（inert，不主動暴露）。
- **Claude API 旁白潤稿** — user 不想處理 API key。`polish_narration.py`、`/polish` endpoint、UI ✨Polish 按鈕全部 revert。

### 8.4 新增依賴 / 安裝
- LibreOffice（winget `TheDocumentFoundation.LibreOffice`）— PPTX→PDF
- pymupdf（pip）— render_slides 用
- rapidocr 3.x（pip `rapidocr`，含 `chinese_cht_PP-OCRv3_rec_mobile.onnx`）

### 8.5 已知小債（明天可清）
- 4 個 task 各自的 `hyperframes/animation.js` 已漂移；今天只在 `task=test/` 加了 hidden flag 支援。等 P3 #10「收斂副本」時統一處理。
- per-slide OCR 完成，per-layer OCR 還沒（解鎖「標紅可疑切塊」UI 用）。
- Bbox reextract 的 mask 是 `|original − background|` 軟閾值，對低對比文字會有缺塊。先用著，後續若卡再升級。

---

## 9. Session 6 — 切圖編輯 v3：Split（2026-06-20）

接續 v1 (merge/hide/reorder) + v2 (bbox 拖拉)，把「這一塊應該切成兩塊」收尾。
選了 **單一切線** 的版本（cut-line），不是兩個獨立 bbox — 覆蓋主要 case 而且資料模型最小。

### 9.1 Schema（與 bbox/hidden/merge_group 平行）

```json
"slide_02": {
  "layers": {
    "slide_02_table_01.png": { "split": { "axis": "x", "at": 0.50 } }
  }
}
```
- `axis: "x"` 垂直切（左/右）；`axis: "y"` 水平切（上/下）
- `at: 0..1` 切點位置，server 端 clamp 到 [0.05, 0.95]
- 兩個 child layer 命名：`<base>_split_a.png` / `<base>_split_b.png`

### 9.2 後端實作
- **`overrides.py`** 加 `split_of(layer_ov)` 與 `split_children(name, bbox, spec)` 兩個 pure helper。
- **`build_composition.py`** 在 layer loop 末端：若 `split_of(o)` 非空，吐兩個 child entry（繼承
  parent 的 z / start / duration / animation / merge_group / hidden），跳過 parent。child image 路徑
  優先用 `edits/<child_name>`，缺檔則退回 parent image（safety）。
- **`pipeline_server.py`** 把 `_reextract_bbox_changes` 升級成 `_reextract_layer_edits`：除了原本
  bbox 重切，還會解析 parent bbox（incoming > overrides.json > metadata.json）並對每個 child 呼叫
  reextract.py 寫進 `output/slide_##/edits/`。

### 9.3 UI（`pipeline-ui/app.js`）
- Layer row 加 `✂` 按鈕（與 👁 / ↑ / ↓ 並列）。
- 點下進 **split mode**：preview 上的該 layer 蓋一條黃色拖把線、兩半 shaded（青/粉）。
- 浮動工具列：Vertical / Horizontal 切換、即時顯示 `at %`、Confirm / Cancel。
- Confirm 送 `{split:{axis,at}}` 到 `/apply`，server 重切兩個 PNG + rebuild composition，UI 自動 reload。
- 切換 slide 或選別的 layer 自動取消 split mode。

### 9.4 驗證
- `overrides.split_children` unit-check：vertical/horizontal、bad axis、at>1 clamp 都 OK。
- writing-os slide 2 端到端模擬：左 panel `table_01` 垂直 50% 切 →
  - composition 從 83 → 84 layers
  - parent 消失，`s02-table-01-split-a`/`-split-b` 出現，bbox `[27,27,452,930]` + `[479,27,453,930]`
  - `output/slide_02/edits/` 下兩個透明 PNG（693 KB + 627 KB）。
- 復原：刪 `overrides.json` 後 rebuild composition byte 不變（83 layers）。
- `node --check app.js` 通過。

### 9.5 未做的（未來可選）
- 第二種模式「兩個獨立 bbox」(non-adjacent split)：schema 升級成 `split:[{bbox},{bbox}]`，UI 多
  一個 mode。目前 cut-line 版本覆蓋主要 case 後就停。
- 切完的 child 再被選中編輯（hide / merge / 再 split）：child name 進 overrides.json 就能用，但
  尚未實機跑過。
- Undo：bbox 沒有，split 也沒有。要做就在 overrides 加歷史。

### 9.6 還沒收的（下個 session 候選）
- ~~**per-layer OCR + UI 標紅低 confidence 切塊**~~ → 已在 §9.7 完成。
- **Pipeline P1 #6 aspect ratio toggle**（9:16 / 1:1 直式輸出，要重跑整條 pipeline）。
- **字幕 forced-alignment**（whisper-timestamped 取代字數比例）。
- **收斂 4 份 `hyperframes/animation.js`、3 份 `task=*/scripts/` 副本到單一 skill**（P3 #10）。
- **HyperFrames adapter**（第 0 節留的門）。

### 9.7 Per-layer OCR + UI review（2026-06-20，同日續做）

接著 v3 split 收完，把切圖編輯 UI 加上「OCR 證據」layer，讓人 review 切塊好不好。

**設計選擇：** 不重新對每個 layer 跑 OCR（會把單 slide ~0.5–2s 變成單 slide ~10s），而是**重用
per-slide `slide_ocr.json` 的 lines**，用「line bbox 中心點落在 layer bbox 內」歸屬。一個 line
只能屬於一個 layer，不會 double-count。

**後端（`build_composition.py`）：**
- 新 helper `_layer_ocr(bbox, ocr_lines)`：return `{text, confidence, line_count}` 或 `None`。
- 走 slide loop 時 lazy-load `slide_ocr.json`；對每個 layer entry 計算 ocr 並 attach（None 就略過，
  composition 沒有 redundant zeroed block）。
- Split children 各自針對 child bbox 重算 — 切完一塊主要文字 + 一塊裝飾，OCR 也分得乾淨。
- 無 `slide_ocr.json` 的 deck（如 writing-os）build_composition 輸出 byte 不變（regression 過）。

**UI（`pipeline-ui/app.js` + `styles.css`）：**
- 每個 layer row 多一條 OCR 證據條：`{confidence%} {snippet}`（青底淡邊）。
- 低 conf（< 0.60）切換成紅底 + ⚠ icon + layer row 邊框紅。`LOW_CONF` 常數在 app.js 頂部。
- 右側 stats card 多 `Low-conf OCR: N`（僅 N>0 時顯示）。
- 加 `esc()` 助手做 HTML escape（layer.name 與 OCR text 都過 escape 防 injection）。

**驗證：**
- writing-os（無 OCR 檔）：rebuild 後 composition.json byte 不變。
- task=test（20 slides, 70 layers）：
  - 61 layers 有 OCR 證據（conf 中位數 ~0.97）
  - 0 低 conf（< 0.60）— RapidOCR + CHINESE_CHT 太準了
  - 9 layers 零 OCR：3 arrows + 2 illustrations + 1 highlight_group（合理：無文字）；
    1 text_block / 1 key_point_card / 1 annotation（**這 3 個值得 reviewer 進 UI 用肉眼確認**）
- `node --check app.js` 過。

**沒做的（未來可選）：** ~~3 個~~ → 全在 §9.8 收尾。

### 9.8 Per-layer OCR review v2：missing flag + 編輯流程 + per-child re-OCR（2026-06-20，同日續做）

把 §9.7 的三個「之後可選」全做完。

#### A. Missing-OCR medium 警告（橘色）
- `app.js` 加 `TEXT_TYPES = {text_block, key_point_card, annotation, table}`。
- layer row 條件：`!ocr || line_count===0` AND `type ∈ TEXT_TYPES` → 橘色 `med` 樣式，◑ icon + `no OCR text · click to add`。
- Stats card 多 `Missing OCR (text type): N`（僅 N>0 時顯示）。低 conf（紅）和 missing（橘）視覺區隔清楚。
- task=test 量化：70 layers 中 3 個觸發橘旗（slide 10 text_block、slide 11 annotation、slide 17 key_point_card —
  跟 §9.7 用肉眼點出的吻合）。

#### B. OCR 編輯流程（click-to-expand modal）
- **Schema**（`overrides.py` 內加註）：`overrides.json.slide_XX.layers.<name>.ocr_corrected: str`（或 null = 取消）。
- **`build_composition.py`** 新 helper `_apply_corrected_ocr(layer_ov, ocr_block)`：有人工校對就 promote 進 ocr block，
  `text` 換成校對版、`confidence` 設 1.0、加 `corrected: true` 旗標。
- 拿掉 `_layer_ocr` 的 80 字 snippet 截斷（要存全文給 modal 用），UI 端靠 CSS `text-overflow:ellipsis` 視覺截斷。
  → task=test 的 composition.json 增加 ~3 個地方文字變長，但其他 layer byte 不變；writing-os byte 不變。
- **UI**（`app.js`）：
  - layer row 加 `data-layer-ocr="1"` + cursor:pointer + click handler。
  - 動態 `#ocrDialog` modal（不污染 HTML），4 區塊：title / meta（type/conf/lines/bbox + ✎ corrected 徽章）/ 大 textarea / actions。
  - Save 邏輯：空字串 + 沒舊校對 = no-op；輸入 = 原始 auto-OCR 文字 = 也視為「移除校對」（避免 redundant null 寫入）；其他 → `applyEdit({ocr_corrected: text})`。
  - Reset to auto-OCR 按鈕送 `ocr_corrected: null`。
  - 加 `esc()` HTML escape 保護 layer.name / OCR text 顯示。
- 校對版 layer row 顯示綠色 ✎ 徽章 + `100%` confidence 框（與 auto-OCR 區隔）。

#### C. Per-child OCR re-run（split 邊界 case 補救）
- **`ocr_slides.py`** 新增 `get_engine()` cache + `ocr_image(path)` 對外 helper（RapidOCR engine 一次性 lazy load）。
- **`pipeline_server.py`** `_reextract_layer_edits` 走完 split-reextract 後呼叫 `_reocr_split_children`：
  1. 讀 `slide_ocr.json.lines`；若空 / 父無 OCR → 直接 return（normal case 不付 cost）。
  2. 對每個 split child：先用 center-in-bbox 試 attribute；命中 → skip。
  3. 沒命中 → lazy import RapidOCR、對 `output/slide_##/edits/<child>.png` 跑 zh-TW OCR、
     寫 cache `output/slide_##/edits/<child_stem>.ocr.json`（shape: `{text, confidence, line_count}`）。
- **`build_composition.py`** split 分支：先讀 `edits/<child_stem>.ocr.json`，若 `line_count > 0` 採用；
  否則 fall back 到 slide-wide center attribution。最後再 `_apply_corrected_ocr`（人工校對永遠最高優先）。
- 端到端測 (task=test slide 02 card_01)：
  - axis=y, at=0.85（child B 是底部 43px 空白）：re-OCR 對 split_b 跑 → 0 lines、cache 寫 60 bytes、最終 composition 該 child 無 ocr 欄位 ✓
  - axis=y, at=0.293（將 line 1 切成「上 19px / 下 43px」）：re-OCR 對 split_a 跑（只剩 19px 頂部小片段）→ 0 lines（RapidOCR 最小 detect 高度限制）。
  - 證明 wiring 正確；recover 文字的成功率視 child 切割後保留多少像素而定。

#### 驗證 summary
- writing-os（無 OCR 檔）：rebuild 後 composition.json **byte-identical**（regression 持續）。
- task=test：rebuild 後 OCR text 變成全文版（70 layers，3 個 row 文字變長，其他 byte 不變）。
- OCR correction 路徑：apply → 校對 → composition 顯示 `corrected:true, conf:1.0`；reset → null 留在 overrides 但 build_composition 視為「無校對」。
- `node --check app.js` 過。
- Server 還沒 restart，user 可以再開一次 server 看新 UI。

#### Skill.md 更新
- §Per-layer OCR evidence 章節擴寫：補 `ocr_corrected` schema、`corrected: true` 旗標、per-child cache (`edits/<child>.ocr.json`) 的存在條件。

### 9.9 Aspect ratio toggle — P1 #6（2026-06-20，同日續做）

UI 加 3 顆 pills `16:9 / 9:16 / 1:1`，點下會 rebuild 整條 pipeline 把 deck 重切成新尺寸。
9:16 給 reels/shorts、1:1 給 IG，把直式短影音輸出解鎖。

**前置觀察：** `WIDTH/HEIGHT` 在 `render_slides.py`、`segment_elements.py`、`build_timeline.py`、
`render_final_video.py` 都是 import 時從 `config.PROJECT["canvas"]` 讀的；既有 task-local
`project_config.json` override 機制只要值寫對，所有 script 自然 pick up。所以實作重點不在參數
傳遞，而在「換完之後正確 rebuild 哪些東西」。

**後端（`pipeline_server.py`）：**
- `ASPECT_PRESETS = {16:9 → 1920×1080, 9:16 → 1080×1920, 1:1 → 1080×1080}`，fps=30 沿用。
- `set_aspect(task_dir, aspect)`：
  1. 將 `{aspect, width, height}` deep-merge 進 `task=*/project_config.json`（保留 fps + render section）。
  2. 清除 stale：`output/slide_*/` 整個刪、`overrides.json` / `composition.json` /
     `narration/narration_timing.json` 刪。**保留** `audio/` 與 `narration_script.md`（旁白不受 aspect 影響）。
  3. 順跑：`render_slides → ocr_slides → segment_elements → build_timeline → build_composition`。
     **跳過 TTS**（已產的音檔仍有效）。回傳 per-step results 與 `/pipeline` 同 shape。
- 新增 `/aspect` POST route（task + aspect），錯誤 aspect 回 400。

**UI（`pipeline-ui/app.js` + `index.html` + `styles.css`）：**
- viewer-actions 加 `<div class="aspect-pills">` — 三顆 pill，當前 aspect 自動 highlight（`currentAspect()` 從
  `composition.canvas.aspect`，或 width/height 比例反推）。
- 點下不同 pill → `confirm()` 警告「會 reset layer 編輯（split / bbox / merge / hide / OCR correction）— 
  bbox 是 aspect-specific 的」。
- 確認 → POST `/aspect`，pills 暫時 disabled、status bar 顯秒數 ticker。
- 完成 → 右下浮現 result panel（icon + 各步驟 elapsed + log 收 80px max-height）、status 標 ✓、
  reload composition。failed step 留 panel + status 標明卡在哪一步。
- 邊界：點當前 aspect = no-op；pending task（沒 composition）也可點，會啟動完整 pipeline。

**驗證（task=test, 50_Startups_Feature_Selection.pdf, 20 頁）：**
| 階段 | 結果 |
|---|---|
| 16:9 → 9:16 set_aspect | 22 個 stale item 刪、project_config.json 寫入 |
| render_slides | 1.8s（20 PNG @ 1080×1920） |
| ocr_slides | 46.5s（每頁 5–20 lines, conf 0.89–0.97） |
| segment_elements | 9.3s（80 layers — 比 16:9 的 70 多 10，預期，垂直尺寸不同切法不同） |
| build_timeline | 0.1s |
| build_composition | 0.1s（80 layers in 9:16 composition） |
| 總時 | ~58s |
| slide_02 PNG 實測 dims | (1080, 1920) ✓ |
| 9:16 → 16:9 revert | 同樣 ~60s，layer count 回到 70 ✓ |

**設計選擇 / 已知限制：**
- 換 aspect 必然 reset overrides — bbox 坐標是 pixel-space、aspect-specific，沒救。UI 警告做到位。
- LetterBox 行為：`render_slides.py` 用 `scale = min(W/page.w, H/page.h)` fit-inside-pad-white，所以
  16:9 deck 換 9:16 後上下會有大片白邊。要做「smart reflow」是另一個專案。
- 沒做：1080p 之外的 preset、bitrate/CRF 跟 aspect 連動。

### 9.10 收斂副本 — P3 #10（2026-06-20，同日續做）

掃完才發現重複比 work_report 估的還多：

| 種類 | 實際發現 | 處置 |
|---|---|---|
| `task=*/hyperframes/{animation.js,index.html,styles.css}` | 5 份，**全 byte-identical**（每次 `build_timeline.py` heredoc 重生） | 抽出 heredoc → skill canonical templates，build_timeline 改用 `shutil.copyfile` |
| `task=*/hyperframes/project.json` | 5 份 **dead output**（沒人 fetch；新版 animation.js 讀 composition.json） | 從 build_timeline 移除寫入；既有 5 個檔刪除 |
| `task=A2Z-animation / HW6-Startup50-Summary / writing-os` 的 `scripts/` | 3 dirs, **全 DRIFTED** 從 skill canonical（沒 split / OCR / overrides 等新功能）| 整目錄刪除（pipeline_server 永遠走 `SKILL_DIR`） |
| `task=HW6-Startup50-Summary/scripts/{rebuild_timeline.py, generate_hyperframes_video_project.py}` | 2 個 **legacy unique scripts**（pre-skill 時代的 all-in-one；硬編 `SOURCE_PDF`） | 一併刪除 |
| `task=InfoGraphic2AIGCdirection/pipeline-ui/` | **新發現** 影子副本，3 檔全 DRIFTED 從 repo-root | 整目錄刪除 |

#### Phase 1 — 抽 hyperframes 模板 → skill canonical
- 新增 `skill-pptx-to-animated-video/hyperframes/{index.html, styles.css, animation.js}` — 單一可編輯來源。
- `build_timeline.py`：刪掉 ~115 行 heredoc（296→181 行）、`HYPER_TEMPLATES = Path(__file__).parent.parent / "hyperframes"`、`copy_hyperframes_templates()` 用 `shutil.copyfile(...)` 三檔複製。**不再寫 `project.json`**。
- 驗證：writing-os 跑 build_timeline 後 3 個 task hyperframes 檔案 md5 = canonical md5 ✓

#### Phase 2 — 刪除 drifted task scripts + 影子 pipeline-ui（13 檔，4 目錄）
```
task=A2Z-animation/scripts/                        (3 檔)
task=HW6-Startup50-Summary/scripts/                (4 檔，含 2 legacy unique)
task=writing-os/scripts/                           (3 檔)
task=InfoGraphic2AIGCdirection/pipeline-ui/        (3 檔影子)
```
Pipeline 永遠走 `pipeline_server.py` → `SKILL_DIR/<script>.py`，這些副本是 import-orphan。

#### Phase 3 — 刪除 dead `task=*/hyperframes/project.json`（5 檔）
非 source-of-truth、沒有 reader、build_timeline 不再寫。一次刪完。

#### Phase 4 — 驗證
- writing-os composition.json rebuild：**byte-identical** ✓
- task=test composition.json rebuild：**byte-identical** ✓
- All skill scripts import OK（render_final_video 例外，它 import time 檢查 ffmpeg，pre-existing 非 P3 影響）
- `HYPER_TEMPLATES` 路徑存在、`ASPECT_PRESETS` 仍生效

#### 量化
| 指標 | Before | After |
|---|---|---|
| `scripts/` 目錄數 | 4（skill + 3 task） | 1（skill 唯一） |
| `pipeline-ui/` 目錄數 | 2（repo-root + InfoGraphic 影子） | 1 |
| Hyperframes template source-of-truth | 5 份 task heredoc 輸出 | 1 份 skill canonical |
| 被掃掉的檔案數 | — | **18**（13 task scripts + 5 project.json） |
| `build_timeline.py` 行數 | 296 | 181（−115，−39%） |
| 改 hyperframes 動畫的成本 | 改 1 處 Python heredoc → 等下次 build_timeline 跑才看得到 | 直接編 `skill/hyperframes/animation.js`，瀏覽器 reload 即看到 |

### 9.11 切圖小債 + P1 #5 + P3 #9a + P3 #9c（2026-06-20，同日掃完所有 leftover）

User 說「可以都做嗎」。掃完 §9.5 + §9.11 列的剩餘項，分 6 個 task 收尾。

#### A. 切圖小債 1 — split child 再編輯實機驗
- 跑了一輪 dry-run，發現 §9.5 「schema 路通了但沒實機跑過」其實**有 3 個真 bug**：
  1. `hidden: true` 設在 child 名上被 build_composition 忽略
  2. child bbox 編輯 → reextract 寫新 PNG 但 composition 仍掛舊 bbox（renderer 會把新 PNG 擺在舊位置）
  3. 對 child 設 split spec 被忽略（recursive split）
- 根因：build_composition 的 split 分支只用 parent 的 override，從來沒查 child 名的 override。
- 修：split 分支現在 `child_ov = overrides.layer(ov, n, cname)` 並把 bbox / hidden / z / animation / start / duration / merge_group / ocr_corrected 都重新套上。
- 驗：同樣的 dry-run 跑完，4 個 child 操作（split → hide one → ocr-correct → bbox-edit）全部如實反映在 composition。
- 仍未做：**recursive split**（child of child）— server `_resolved_bbox` 沒辦法從 chained overrides 解出 child 的 parent_bbox。算 v4 的範圍。

#### B. 切圖小債 2 — Non-adjacent split (兩個獨立 bbox)
- **Schema**：`split` 變成 discriminated 兩種：
  - `{axis, at}` → 自動推導兩個相鄰 child（原版）
  - `{bboxes: [[x,y,w,h], [x,y,w,h]]}` → 兩個明確 bbox，可以非相鄰
- `overrides.split_of()` 回 `{kind: 'cut'|'bboxes', ...}`；`split_children()` 兩種都吃；degenerate (`w<=0` 等) 直接 reject。
- **UI**：split toolbar 加 `Cut | Bboxes` mode pills（Vertical/Horizontal 只在 cut mode 顯示）。
  - Bboxes mode：在 stage-preview 直接畫兩塊半透明 A/B（青/粉）matrix，各有 4 corner handles + 中心 move zone，可獨立拖拉/resize、可重疊、可非相鄰。
- 後端零改動 — `_reextract_layer_edits` 跟 `_reocr_split_children` 都已經是「給 child name + child bbox」就好，自動 work。
- 端到端跑了一次 task=test slide 02，兩個非相鄰 region 都正確切出 PNG + 自動 re-OCR 其中一個 (1 line, conf 1.0)。

#### C. 切圖小債 3 — Undo
- **Schema**：`task=*/.overrides_history/NNNN.json` ring buffer，cap = HISTORY_MAX = 20。
- **Server**：
  - `_snapshot_overrides(task_dir)` 在每次 `apply_overrides` 之前快照舊 overrides.json（**例外**：notes-only payload 不快照 — notes 每個 keystroke 都 fire，會瞬間吃完 buffer）。
  - `undo_overrides(task_dir)`：pop 最近 snapshot 回寫；如果 narration/voice 在 current 跟 snapshot 之間有差 → 自動重跑 TTS + timeline；最後一定重跑 build_composition。
  - 新 endpoints：`/undo`、`/history-depth`；`/apply` response 多 `history_depth` 欄。
- **UI**：topbar 多一顆 `↶ Undo`；loadTask 後 `refreshHistoryDepth()` 同步；按下 disable→re-enable + reload composition。
- 驗：hide → notes(no snap) → undo，history 1→1→0，overrides 從 `{slide_02.hidden:true}` 還原為 `{}`，全程預期。

#### D. P1 #5 — per-layer `start` 真正搬 timeline
- 之前：`segment_elements.py` 把 start baked 進 metadata.json；TTS 重跑（audio 變長）後 start 不會跟著重算。
- 改：
  - `segment_elements.py` 新增寫入 `entry_index`（每個 layer 排序後位置）+ annotation 寫 `parent_index`（用 `id(entry)` map 找）。**仍保留** baked `start` 為 fallback。
  - `build_timeline.py` 新 `recompute_layer_starts(layers, duration)`：用同樣的 cue_gap 公式從**當前** duration 重算；無 `entry_index` 的舊 metadata 自動 fallback 到 baked start（→ 舊 task byte-identical）。每 slide 把 `layer_starts: {name: start, ...}` 寫進 narration_timing.json。
  - `build_composition.py` 改讀 `timing[key].layer_starts.get(name)` 為 baked_start，fallback 到 metadata。
- 驗：
  - writing-os（舊 metadata 無 entry_index）build_timeline + build_composition 後 composition byte-identical（fallback path），timing.json 多了 `layer_starts` 欄位但內容與原 baked starts 一致 ✓
  - Unit-style 測：4 layers + 1 annotation 子層，duration 6.55s → 10s 時，start 自動重新分布、annotation 跟 parent 同步推後 ✓

#### E. P3 #9a — 字幕 forced-alignment
- 新 script `skill/scripts/align_subtitles.py`：
  - 嘗試 `import whisper_timestamped`；**沒裝就 print 訊息 + return**（pipeline 仍 ok，舊 SRT 保留）。
  - 裝了：load `base` model (~140 MB) → 對每個 slide MP3 跑 zh transcribe → 拿 word-level timestamps → 用 char→cue map greedy 對 cue 文字配對；coverage < 40% 自動 fallback 到「按字數比例分配在實際 speech_lo..speech_hi 範圍」。
  - 輸出：直接覆寫 narration/subtitles.{srt,vtt}，保留 cue 文字、refine 時間戳。
- Pipeline server 把 align_subtitles.py 插進 `run_pipeline` plan 在 build_timeline 之後、build_composition 之前。
- 沒實機跑 forced-alignment（沒 pip install whisper-timestamped），但 graceful skip 驗證過。要實機跑 user 自己 `pip install whisper-timestamped` 即可。

#### F. P3 #9c — HyperFrames adapter stub
- 新 script `skill/scripts/export_hyperframes.py`：讀 composition.json → 輸出
  `hyperframes/project.hf.json`（13 scenes / 83 assets 在 writing-os 上驗證）。
- 結構：top-level `{width, height, fps, transition, caption_style, scenes:[{index, duration, audio_src, narration, assets:[{src,x,y,width,height,z,in_time,in_duration,animation}]}]}`。
- **檔頭明確標 _speculative**：mapping 字典在 `_HF_FIELD_MAP` / `_HF_ANIM_NAMES`，user 拿到真實 HF schema 之後把 dict 改一改就能完。
- 沒接 UI 按鈕 — user 還沒給 HF spec 之前綁 UI 會誤導；命令列跑得到就先停在這。

#### 驗證 summary（整個 part 6）
- `overrides.split_of()` 兩種 shape：unit ok。
- writing-os composition rebuild **byte-identical**（多輪：split-child re-edit fix / non-adjacent / Undo / P1 #5）。
- task=test composition rebuild **byte-identical**。
- `align_subtitles.py` whisper 沒裝時 graceful skip 訊息正確。
- `export_hyperframes.py` 跑 writing-os 成功 (13 scenes / 83 assets, 都用 `_speculative` 標頭警告)。
- `node --check pipeline-ui/app.js` 過。
- `python -X utf8 ast.parse pipeline_server.py` 過。

#### 量化（session 6 part 6 累計）
| 指標 | Before | After |
|---|---|---|
| Split 模式 | 1（cut 線） | 2（cut + bboxes） |
| Child 操作可信度 | 3 個操作 broken | 全部如實反映 ✓ |
| Undo | 無 | ring buffer 20 + UI 按鈕 |
| Per-layer start 重算 | 從未 | TTS 重跑後自動重算（舊 task byte-identical） |
| Forced-alignment | 字數比例 | 字數比例 + 可選 whisper-timestamped |
| HyperFrames adapter | 無 | stub + speculative schema |
| 待續主軸項 | P1 #5 / P3 #9a / P3 #9c / 切圖 3 小債 | **全收**（recursive split 改 v4） |

### 9.12 把剩下沒做的也都做了（2026-06-20，同日 part 7）

User 又說「把沒做的都做一下」，掃 §9.11「沒做」三件全處理。

#### A. Recursive split — split a split child
- 之前 known limit：「server 無法從 chained overrides 解出 child 的 parent_bbox」。
- 修：
  - **`pipeline_server.py`** 加 `_split_parent(name)` regex `^(.*)_split_(a|b)(\.[^.]+)$`，剝掉最右一個 `_split_<x>` token；`_resolved_bbox()` 變成遞迴：找不到直接 metadata + 不是 split 子嗣才回 None，否則上推 parent name + parent split spec + `split_children()` 解出 child bbox。
  - **`build_composition.py`** 把 split 分支 inline code refactor 成 `_emit_split_child(parent_entry, cname, cbbox, …)`，這個 helper 在處理完 child override 後**再查一次** `overrides.split_of(child_ov)` — 有就遞迴生 grandchildren，沒有就 return 單 child。任意深度都自動展開。
- 驗（task=test slide 02）：
  - level 1：parent `card_01` 用 axis x at 0.5 切成 `split_a` (102, 106, 394, 287) + `split_b` (496, 106, 393, 287)
  - level 2：對 `split_a` 用 axis y at 0.5 再切 → `split_a_split_a` (102, 106, 394, 144) + `split_a_split_b` (102, 250, 394, 143)
  - 結果 layers list：`split_a_split_a` / `split_a_split_b` / `split_b`（三層共存，parent 跟一階 split_a 都消失）✓
- writing-os composition rebuild **byte-identical**（refactor 純內部，沒 split override 的 task 路徑一致）。

#### B. Forced-alignment 實機跑（writing-os 13 slides）
- 環境準備：
  1. `pip install whisper-timestamped` → 帶 torch 2.12.1 + openai-whisper 20250625 + tiktoken 等（**11 packages, 200+ MB**）
  2. 首次跑炸了：openai-whisper 內部 subprocess `ffmpeg`，但 ffmpeg 不在 PATH → `[WinError 2]` 全 13 slide 失敗。
  3. `npm install` 補回 `node_modules/ffmpeg-static/ffmpeg.exe`（package.json 已宣告但沒裝）。
  4. **修 `align_subtitles.py`** 加 `_ensure_ffmpeg_on_path()`：開頭看 PATH 沒 ffmpeg 就 prepend `node_modules/ffmpeg-static`（cwd or repo root）。
- 跑第二次：模型下載 ~140 MB（首次），轉錄 13 slides 共 ~40 秒（CPU base model，515–721 frames/s）。
- 結果：
  - **13/13 slides aligned, 67 cues rewritten** ✓
  - 每個 cue 時間戳跟原 char-proportional 差 50–500 ms（refine，不是混亂）
  - 拿 cue 文字 diff（去 timestamp 比較）= **identical** — 文字一字不變、純時間戳精修
  - 範例：cue 1 從 `00:00:00,000 → 00:00:01,997` 變成 `00:00:00,140 → 00:00:01,970`（開頭 140 ms silence 正確識別出來）
- 落地：`subtitles.srt`/`.vtt` 直接覆寫成 word-boundary 對齊版。

#### C. HyperFrames adapter — user 確認沒有 HF spec，把 stub 寫好看一點就停
- User 選「沒有，把 stub 寫好看一點就停」。
- 強化 `export_hyperframes.py`：
  - Schema 多加：`scene_background` (background.png 路徑)、`scene_cues` (per-asset entry 時間軸)、`subtitles_srt_src` / `subtitles_vtt_src` global path、asset.ocr 區塊 (text + confidence)。
  - 新 `--list-fields` CLI flag：印出 composition 有的資料量化 + 當前 `_HF_FIELD_MAP` 跟 `_HF_ANIM_NAMES` 兩個字典所有 mappable 欄位。Future user 拿到真實 HF schema 時可以直接對表編。
  - 保留 `_speculative` 標頭 + `[!] speculative schema` 警告 print，避免人誤用。
  - docstring 重寫：列出已 mapped 資料（per project / per scene / per asset）讓人不必重發現。
- 驗：writing-os 跑 export，輸出 `hyperframes/project.hf.json`（13 scenes / 83 assets，含 background_src + cues 13×N + 兩個 subtitles 路徑），`--list-fields` 印出 18 個欄位 + 7 個動畫名映射；regression 結果未改。

#### 驗證 summary（part 7）
- writing-os + task=test composition rebuild **byte-identical**。
- All skill scripts (含新的 align_subtitles + export_hyperframes) import OK。
- `node --check app.js` 過。
- Forced-alignment 端到端：13/13 OK，文字 zero-diff、timings refined。
- HF stub 端到端：list-fields 印出 OK、export 結構含新欄位 OK、speculative marker 在。

#### 累計 session 6 ANALYSIS_REPORT P3 #9c
- 之前是 🟢（stub + speculative）。user 確認沒有真實 HF spec，這條的 ACTIONABLE 部分就是「寫好 stub」，已收尾 → 保留 🟢（不 promote 到 ✅，因為「真的接 HF」需要 HF schema 才能完成）。

#### 剩餘真的待續（不在本 session scope）
- **真實 HF 接通**：等 user 拿到 HF 匯出檔之後改 `_HF_FIELD_MAP` / `_HF_ANIM_NAMES` 即可，腳本架構已備。
- **whisper 大模型**（small/medium/large）：目前用 `base`（~140 MB，CPU 跑 20-40s per slide）。換大模型準度更好但慢 3–10×；要改就改 `MODEL_NAME` 常數。
- **GPU acceleration**：torch + CUDA 可加速 whisper 10–30 倍。未配置。
- **UI 不知道 SRT 已 aligned**：純後台事，UI 沒任何旗標顯示 cue 是 baked 還是 aligned。Cosmetic，可以等。

---

## 10. 對照 ANALYSIS_REPORT §8 變化（session 6 結束）

```
P0（地基）
  ✅ 1–4 全完成

P1（順序耦合）
  ✅ 5. 解耦 TTS↔切圖（per-layer start 在 timeline 重算）                ← 今日 part 6.D ★
  ✅ 6. aspect ratio toggle（16:9 / 9:16 / 1:1，rebuild pipeline）        ← 今日 part 4 ★

P2（智慧編輯）
  ✅ 7. OCR + confidence（per-slide + per-layer review UI + 校對 + per-child re-OCR） ← 今日 part 2/3 ★
  ✅ 8. UI 編輯寫進 overrides
        v1 merge/hide/reorder ✅
        v2 bbox 拖拉 + 自動 reextract ✅
        v3 split (cut-line) ✅                                ← 今日 part 1 ★

P3（打磨）
  ✅ 9a. 字幕 forced-alignment（whisper-timestamped 可選，未裝就 skip）   ← 今日 part 6.E ★
  ✅ 9b. PPTX 直通
  🟢 9c. HyperFrames adapter（stub + speculative schema，等真 HF spec）   ← 今日 part 6.F ★
  ✅ 10. 收斂 scripts/ 副本 + hyperframes templates + 影子 pipeline-ui   ← 今日 part 5 ★
```

`Last 大幅變動：2026-06-22（session 8：PDF 上傳 null guard 修復、刪除 task 按鈕、旁白 auto_narration 自動化、切圖 merge-span 通用上限消 blob、per-deck seg_overrides.json + no_annot + 真不規則(irregular)切圖、PPTX 部署打包 requirements/Dockerfile/DEPLOY 但擱置）。詳見 §12。`

`session 7（2026-06-22）：真 HF export → MP4、Preview-first UI reframe、`/apply` 收 `caption_style`、`/` redirect、SRT CRLF parser fix、preview-card overflow fix、播放期 timeline/badge 隱藏`

之前：2026-06-20（session 6：切圖 v3 split + 兩 bbox + child fix + recursive + per-layer OCR v1/v2 + aspect ratio + 收斂副本 + Undo + per-layer start 重算 + forced-alignment 實機跑 + HF adapter stub 強化）

---

## 11. Session 7 — Real HF export + Preview-first UI 大改（2026-06-21 ~ 06-22）

> 本節是今天的工作；§10 之前是 sessions 1–6 的彙整。先看 §11.5 拿到「明天的待辦」。



兩件主軸：(A) `export_hyperframes.py` 之前是 speculative JSON stub，這次把它換成可實際被
HeyGen `hyperframes` CLI 接受並渲染出 MP4 的真 HF 專案。(B) Pipeline-UI 從「演算法調校用的編輯
器」reframe 成「給最終使用者預覽 + 選配音/字幕」的瀏覽器，UI 大瘦身、加新控制項。

### 11.1 真 HF export（`export_hyperframes_html.py`）

**起因：** 原本 stub 產的是猜的 JSON（`_HF_FIELD_MAP` 標 _speculative）。實際上 HF 不吃 JSON——
它吃 **HTML composition + GSAP timeline + `data-start/data-duration/data-track-index` 屬性**。
User 想試但不知道怎麼生，直接讓我幫他用 HF 試試看，於是順著 `my-video/` 腳手架（`hyperframes.json`、
`meta.json`、`index.html`）跟 `npx hyperframes docs` 的文件把真版本寫出來。

**輸出結構：**
```
task=*/hyperframes-export/
  package.json, hyperframes.json, meta.json
  index.html               # main composition：每張 slide 一個 sub-comp embed + audio
  compositions/slide_NN.html  # 13 個 sub-composition，bg + layer imgs + GSAP timeline
  assets/                  # 79 個 PNG/MP3 flatten 複製進來
```

**踩到的坑（全部已修進 exporter）：**
1. `background.png` 在每張 slide 同名 → 互相覆蓋。flatten 時若檔名沒帶 `slide_NN_` 前綴就補上。
2. HF lint 把 sub-composition 內的 `<audio>` flatten 到 root timeline 視為都從 t=0 起算 → 全部
   重疊。把 audio 從 sub-comp 抽到 root index.html、按 cursor offset 排好。
3. `overlapping_clips_same_track` error：13 個 audio 在 track 0 接續（cue1.end == cue2.start
   被當重疊）→ 每個 audio 分配獨立 track index (0..12)，sub-comp embed 用 track 13..25。
4. `media_missing_id`：audio/background 都加 id。
5. HF 自帶 chrome cache 解壓壞掉（`browser ensure` 報 found 但只有 LICENSE 沒 .exe）→ 文件記
   `Expand-Archive` 手動解壓的繞道方法。
6. FFmpeg 不在 PATH → 用 `node_modules/ffmpeg-static/ffmpeg.exe` + `node_modules/ffprobe-static/bin/win32/x64/ffprobe.exe` prepend PATH 再跑 render。

**最終驗證（writing-os, 13 slides）：**
| 階段 | 結果 |
|---|---|
| `hyperframes lint` | 0 errors, 13 warnings（`gsap_studio_edit_blocked`——用 GSAP 控動畫 = Studio 不能拖編輯，advisory 不擋 render） |
| `hyperframes validate` | No console errors |
| `hyperframes inspect` | 0 layout issues across 9 samples |
| `hyperframes render -o out.mp4` | 7075 frames captured、encoded、assembled → **19.7 MB MP4, 6 min 3 秒** ✓ |

旧的 `export_hyperframes.py`（speculative JSON）保留作參考；ANALYSIS_REPORT §8 P3 #9c 從 🟢 升 ✅。

> 一個未追的 warning：render log 顯示 `Sub-composition timelines not registered after 45000ms`——
> sub-comp 的 GSAP timeline 註冊 key 是 `slide_NN`，host 期待的是 `slide_NN-embed`（mismatch），
> 所以 enter 動畫可能沒跑、layer 是硬切。MP4 主體（bg + audio + 順序）都正確，動畫是
> cosmetic gap，可在下一輪修。

### 11.2 Pipeline-UI reframe — preview-first

User 直接釐清產品定位（已存記憶 `feedback_ui_scope`）：「我寫好的切圖邏輯是主角，使用者只是預覽切圖、
選配音/字幕」。原本 UI 是 6 個 session 累積的編輯器，整面 merge/hide/split/bbox-drag/OCR-correct/
aspect-toggle/Undo 按鈕，跟使用者想要的不對。

**砍掉（編輯類，~500 行 app.js 走進 dead code path，CSS 用 `body.preview-mode` 全 hide）：**
- Topbar 的 `↶ Undo`
- Viewer-actions 的 aspect pills（16:9/9:16/1:1）
- Layer row 上 `👁/↑/↓/✂/✎` + Ctrl-click 多選 + Merge action bar
- Bbox 拖拉 handles
- Split mode + toolbar（cut-line / bboxes 兩模式）
- OCR correction modal
- Layer 編輯面板（start/dur/anim）
- Layer timing panel（每 layer 一行 `title ── 0.45s` 列表）
- Notes section
- "Open in HyperFrames" 按鈕（沒接到 stub）

> **後端全部保留**：`overrides.json` schema、`/apply` 的 split/bbox/hidden/ocr_corrected 處理、reextract、Undo ring buffer 都還在；演算法調校時用 curl + 手寫 overrides.json 還是能用。

**新增（user 想要的 4 個）：**
| 區塊 | 內容 |
|---|---|
| **預覽模式切換**（viewer header） | `動畫預覽`（layer 帶 fade-in/zoom-in 進場，按 ▶ Play slide 才真播）／`切圖檢視`（不跑動畫、所有 layer 全顯示 + 彩色 outline + `數字 · type` 標籤，按 type 上色：title 青、table 紫、illustration 綠、icon 粉、annotation 桃紅、…） |
| **Voice card** | Language 下拉（zh-TW / zh-CN / en-US / ja-JP）→ 自動切換 Voice 下拉的可選聲線（每語言 2–4 個 edge-tts 預設聲線）；語速從純 input 改成 -50%~+100% slider，標籤同步「+38%」格式字串 |
| **Subtitle card** | on/off toggle、字型大小 slider（8–32）、字色 picker、底色 picker（自動 bake 40% alpha）、即時預覽框（color/size 同步 CSS）。Apply 後 server 寫進 `caption_config.json`（deep-merge）+ 重建 composition；下次 MP4 export 自動 pick up |
| **顏色主題** | dark / light / slate / warm 已存在，移到 topbar 邊角 |

**後端 `/apply` 新增 `caption_style` 路由：**
```python
if isinstance(incoming.get("caption_style"), dict):
    cap_path = task_dir / "caption_config.json"
    merged_caps = deep_merge(load_json(cap_path) or {}, incoming["caption_style"])
    write_json(cap_path, merged_caps)
```
不重 TTS、只重建 composition（cheap）。

**`pipeline_server.py` 加 `/` → `/pipeline-ui/` 302 redirect：**
舊網址 `http://127.0.0.1:9001/` 雖然回傳 `pipeline-ui/index.html` 內容、但相對路徑（`styles.css`、`app.js`）會被瀏覽器解到根目錄 → 404 → 前端整個死。User 第一次抱怨「什麼都看不到」就是這個。redirect 後不管打哪個都會到正確的 base。

### 11.3 路上爆出的 5 個 bug（已全修）

1. **Top-level `addEventListener` on null（init 沒跑）**：refactor 後 `showSkipped` / `hfBtn` 變成
   `null`，但 `showSkipped.addEventListener(...)`、`hfBtn.addEventListener(...)` 是 top-level 同步
   呼叫 → throw 後整個 script 中斷 → 連 init() 都沒跑 → task select 空白。修成 `?.addEventListener`。
2. **CRLF SRT parser**：Windows SRT 是 `\r\n` 結尾，舊 split 用 `/\n\n+/` → 在 `\r\n\r\n` 上**永遠不
   切**，整個檔案變成第一個 cue 的 text。播放時整頁字幕（含 `2 / 00:00:05,140 --> ... / 示範...`
   的列表）直接糊上 slide。User 報的「整頁字幕 + 時間戳擋住」就是這個。
   修法：`raw.replace(/\r\n?/g,'\n')` 後再 `split(/\n{2,}/)`。
3. **Preview-card overflow clipping**：原 CSS `width:100%; aspect-ratio:16/9` 在 card 較矮時 height
   超過 card 被 `overflow:hidden` 截掉，user 報「composite 看不到右上角」——其實切圖 0 差異，
   只是視覺被剪。改成 `width: min(100cqw, calc(100cqh * 16/9))`（container queries）兩維都吃緊邊。
4. **Per-layer index badges + timeline 在播放期殘留**：層的 `1 / 2 / 3` 編號 + 底部 Layer timing
   面板（每行 `title ──── 0.45s`）被當成「投影片字幕」。preview mode 隱藏整面 timeline panel，
   `.playing` 狀態額外隱藏 layer-idx + seg-tag。
5. **HF Chrome cache 半安裝**：`browser ensure` 報 ok 但 cache 只有 LICENSE 沒 exe → 手動
   `Expand-Archive` zip 出 173 MB chrome-headless-shell.exe，validate/render 才能跑。

### 11.4 量化（session 7）

| 指標 | Before | After |
|---|---|---|
| 真的能被 HF CLI 渲染的 export | ❌（猜的 JSON） | ✅ MP4 19.7 MB, 6m 3.3s |
| UI 控制項數 | 19（多數編輯類） | 9（純預覽 + 選配音/字幕） |
| 編輯按鈕在 user 視野 | 整片 | 0（後端保留） |
| `pipeline-ui/app.js` 行數 | 1074 | 1083（refactor，主要砍綁定、加 voice/subtitle/preview-mode handlers） |
| `pipeline-ui/styles.css` 新增 | — | +preview pills、voice grid、subtitle grid、sub-preview、`.preview-mode` hide rules |
| 後端新支援 | overrides only | `+ caption_style` deep-merge into caption_config.json |
| Server `/` 行為 | 直接服務 pipeline-ui/index.html（相對路徑斷掉） | 302 → `/pipeline-ui/` |

### 11.5 剩餘待辦（明天 / 之後 candidates）

**短期、user 用得到：**
- HF sub-composition timeline key 改成 `<id>-embed` 對齊 host → enter 動畫真的會跑（目前 MP4 內動畫被
  HF 跳過、layer 硬切）。
- Voice apply 後右側 audio 預覽 audio src 不自動換成新 voice 的 MP3（需要等 TTS 重跑回傳後 re-load
  `${tRoot}/audio/slide_NN_voiceover.mp3?v=${S.rev}`，已有 rev 機制但 UI sync 沒 verify）。
- Subtitle on/off：目前只是 UI 的設定旗標；render 預設永遠生 2 個 MP4（無/有字幕），UI 該根據旗標選哪個下載 / 預覽。export-MP4 對話框要加這個選項。
- Preview-mode 切換的 active class 寫進 localStorage，重新整理保留選擇。

**中期、結構性：**
- 真把今天砍掉的「編輯」拿去 `editor.html`（同一份 app.js，body 沒 `.preview-mode` 就重新出現所有編輯按鈕），切兩個 entry：給 user 的預覽 UI 跟 給 algorithm 作者的 debug UI。目前是 CSS hide 但 code 還在跑。
- UI sidebar 加 thumbnails（小圖，slide list 文字 + 縮圖更直觀）。
- HF export 加字幕：把 `subtitles.srt` 嵌進 HF composition 或用 letterbox + ASS 燒在 render 前。目前 HF MP4 沒字幕（只是純動畫 + 旁白）。

**冷凍區（不在本 session scope）：**
- whisper 大模型 / GPU 加速
- recursive HF export（grandchild split）
- UI 顯示 SRT 已 forced-aligned 旗標

---

## 12. Session 8 — PDF 上傳體驗修復 + 切圖通用化/真不規則 + 部署打包（2026-06-22）

這個 session 回到「以 PDF 上傳為主」的使用體驗，修掉一個會讓 PDF 上傳整個壞掉的前端 bug，
把旁白生成補成不會卡住的自動步驟，並針對使用者實際看到的切圖問題做了兩件事：一個**通用**的
演算法修正（消除橫跨整張的 blob，對所有 deck 有效）+ 一套**per-deck** 的 `seg_overrides.json`
微調機制（含真不規則切圖）。最後把 PPTX 部署打包寫好但**先擱置**（user 決定先專注 PDF）。

### 12.1 PDF 上傳 bug 修復（前端 null guard）
- 症狀：上傳 PDF 後跳 `Failed: Cannot set properties of null (setting 'innerHTML')`。
- 根因：session 7 preview-first 重構把 `layerList` / `layerEdit` 等編輯節點設成 `null`，但
  `showPendingTask()`（PDF 上傳後、還沒跑 pipeline 的 pending 畫面)仍直接 `layerList.innerHTML=''`、
  `layerEdit.hidden=true`，沒有像第 359 行那樣加 `if(...)` guard。
- 修：`pipeline-ui/app.js` 兩行補上 `if(layerList)` / `if(layerEdit)` guard（與既有模式一致）。

### 12.2 刪除 task 按鈕（user 需求）
- UI 下拉選單旁加 `🗑 刪除`（`index.html` + `app.js`，含 `confirm()` 二次確認、刪完自動刷新選單）。
- 後端 `pipeline_server.py`：新增 `/delete-task` route → `delete_task()`（刪資料夾 + 從 task-index.json 移除）
  + `_remove_from_task_index()`。**安全防護**：只允許刪 ROOT 底下、名稱以 `task=` 開頭的資料夾,
  實測擋下 `task="."`（刪 ROOT）。

### 12.3 旁白自動化（pipeline 不再卡在 narration missing）
- `run_pipeline` 在 OCR 之後、TTS 之前插入 `auto_narration` in-process step：缺 `narration_script.md`
  時自動用 OCR seed 一份草稿再繼續，pipeline 一律跑得完；已有旁白則跳過用 user 的。
- `make_starter_narration` 改良：新增 `_clean_ocr_lines()` — 丟掉 footer 浮水印（模糊比對 `notebook`）、
  單字碎片、純數字行（圖表刻度），並猜每張標題放進 `## Slide NN - 標題`。
- **誠實結論（已跟 user 對齊）**：沒有 LLM 的 OCR 草稿品質有天花板（它不理解內容、只是文字片段堆疊），
  規則救不回來。要好旁白只有三條路：**Claude 在 Claude Code 裡手寫(免費,需我在場)** / 後端接 LLM
  API(要 key+費用) / user 自己在 UI 寫。User 沒有 API key → 結論是**旁白交給 Claude 手寫**,
  OCR 自動草稿退回「沒有 LLM 時的最後備援」。

### 12.4 task=test 手寫旁白
- 直接讀 12 張投影片圖,寫了一份通順繁中 `narration_script.md`(標題/數字對齊投影片實際內容),
  原 OCR 草稿備份到 `narration/narration_script.ocrdraft.bak.md`。重生 TTS/timeline/composition。

### 12.5 切圖**通用**修正 — 消除「橫跨整張」的 blob
- 問題:slide 06/09/10 把密集內容(金字塔、頭像環)整片併成一塊 `table`/`chart`,動畫只能整塊淡入。
- 精準定位:`collage_cluster`(0.30)和 `detect_cards`(0.48)本來就有「整片就別合」上限,但
  **`merge_pass` 和後面的 `absorb` 迴圈沒有** → blob 從那裡溜出來(slide 10 在 merge_pass、09 在 absorb)。
- 修(`segment_elements.py`):新增 `spans_slide()`(both-dims > 0.78W × 0.6H)並**集中寫進 `absorb()`**
  (回傳 bool,span 就拒絕)+ piece 階段的 merge_pass 也包一層;5 個 absorb 呼叫點全改成「成功才移除」,
  只有 MAX_LAYERS 收尾那個 `guard=False` 放行(壓低層數優先)。
- 驗證(task=test 12 張基準逐張比對):**9 張 byte-identical(零退步)**,06/09/10 的整張 blob 全消除。

### 12.6 per-deck `seg_overrides.json` + `no_annot` + 真不規則(`irregular`)
- **載入機制**:`segment_elements.py` 的 `OVERRIDES` 從空 dict 改成 `_load_seg_overrides()` 讀
  `<task>/seg_overrides.json`(keyed by slide;沒檔就空,其他 deck 完全不受影響)。沿用切圖腳本**本來
  就內建但一直空著**的 `apply_overrides`(merge/suppress/order),符合 pull 的 per-task JSON 慣例,
  不再 fork 整支腳本。
- **`no_annot` flag**:被指定的 chart/table 不要再自動把紅色標記抽成獨立 annotation 層(slide 10
  金字塔的 tier 圖示就是這樣冒出來的)。
- **`irregular` flag(真不規則切圖)**:開啟後圖層 alpha = 元素真實輪廓、四角透明,且**背景重建只挖
  掉形狀**(不是整個矩形,保留周圍紙張紋理)。
  - 第一版用墨水遮罩(深色|高彩度)→ 漏掉金字塔**淺灰底層**(灰 215、彩度 16,既不深也不飽和)。
  - User 建議「邊緣偵測 + 連通判定同一區塊」。改成 **「跟紙張中位色差異 > 22 → 前景」+ close + 填補輪廓**:
    淺色平面填色也算進同一區塊。四層金字塔(含灰底)完整切出。
  - 每次都用「重組原圖 = 0 像素差」驗證,確保不規則 + 形狀背景填補沒破綻。
- task=test slide 09 = 整張一塊;slide 10 = 三角形(真不規形、含灰底) + 四段文字各一塊,移除 arrow/annotation 雜訊。

### 12.7 「為什麼切圖都是矩形」調查結論
- 圖層匯出本來就是 RGBA、有 alpha 機制(支援不規則),但**預設只有 annotation 會套遮罩**,其餘
  (chart/text/illustration…)走 `alpha=255` 不透明矩形(為了讓紙張紋理無縫接回:背景會把整個 bbox
  填純色)。實測 task=test 各層 alpha 全 1.00 證實。**新舊版(git 6a86e82 vs 現在)這段邏輯一模一樣
  —— 不是被改壞、也不是這次動到的**,是一直以來的預設。`irregular` flag 就是給需要時 opt-in。

### 12.8 PPTX 部署打包(寫好,先擱置)
- User 想支援 PPTX 上傳,並擔心「給別人用還要叫人裝 LibreOffice」。釐清:**轉檔是 server 端做的,
  開瀏覽器的人不用裝;需要的只有跑 server 那台**。系統相依其實有兩個:LibreOffice(PPTX→PDF)+
  ffmpeg/ffprobe(音長/render),都不是 pip 裝得到。
- 新增檔案(惰性,不影響 PDF 流程):
  - `requirements.txt` — 精確 7 個 Python 套件(pymupdf/opencv-python/numpy/Pillow/edge-tts/rapidocr/onnxruntime)。之前根本沒有。
  - `Dockerfile` — `python:3.12-slim` + apt 裝 libreoffice + ffmpeg + fonts-noto-cjk + opencv 系統庫 + pip。一個 `docker run` 零手動安裝。
  - `.dockerignore`、`DEPLOY.md`(Docker / 各 OS 直裝兩條路 + 掛 volume 持久化 + CJK 字型提醒)。
  - `convert_pptx_to_pdf.py` 的「soffice not found」錯誤訊息改成指向 Docker/DEPLOY.md/「也可傳 PDF」。
- User 選「每個人各自在本機跑」後,決定**先擱置 PPTX、以 PDF 為主**。Dockerfile 尚未實際 build。

### 12.9 關鍵決策 / 取捨(帶去跟相關人確認需求用)
- **旁白**:品質 vs 全自動 vs 免費,三者不可兼得。要全自動+好+給別人獨立用 → 必須後端接 LLM API(有費用)。
- **PPTX**:server 端轉檔;「給別人各自跑」每台仍需 LibreOffice+ffmpeg,差別只在「Docker 一鍵 vs 照 DEPLOY.md 直裝」。

### 12.10 驗證 summary
- `node --check pipeline-ui/app.js` 過;`ast.parse` pipeline_server.py / segment_elements.py / convert_pptx_to_pdf.py 全過。
- `/delete-task` 實測:guard 擋下刪 ROOT、正常刪 throwaway task + 同步移除 index。
- 切圖修正:task=test 12 張對基準,9 張零退步、3 張 blob 消除。
- 不規則切圖:task=test slide 10 重組原圖 = 0 像素差。
- Server 收工前已停(背景 task)。

> 本 session 未提交的本地狀態:`task=test/`、`task=test2/`(測試 deck 資料)與 `task-index.json` 留在
> 本機不推送;推送的是程式 + 打包檔 + 文件。

---

## 13. Session 9 — 切圖通用化(broken-border 卡片)+ audio 時長 bug 修復(2026-06-22 續)

延續 SESSION_HANDOFF 的 testtt 待辦「Slide 2 箭頭變大」。使用者點出原則問題:
**不該凡事 override**——別的投影片遇到一樣問題還是會壞;且 skill 初衷已寫明
優先改演算法。於是改用通用修法。

### 13.1 根因(用量測,不是猜)
- testtt slide 2 的 Marketing Spend 卡只剩中間「Spend」字殘留成 arrow,卡片
  邊框 + 「Marketing」字併進了背景(機器/漏斗那塊大 blob)。
- 實測 `detect_cards`:這張卡的洞**有**被找到,尺寸/矩形度/內部 ink 全過,
  唯獨卡在 `border_ring_fraction`——右邊框緊貼漏斗、手繪線斷裂只有 **0.54**,
  低於四邊取最小值的 0.85 門檻 → 被當 phantom hole 丟棄。

### 13.2 通用修法(`segment_elements.py`)
1. `border_ring_fraction` 拆出 `border_ring_sides()`(回傳四邊),新增
   `has_card_border()`:放寬成「最強三邊 ≥0.85 且最弱邊 ≥0.50」,容許一邊被
   相鄰圖形蓋斷的真卡片。
2. 這類「弱邊卡」**不參與 `card_adjacent` 串接**,且**與強卡重疊就丟棄**——
   避免弱邊洞把兩張真卡橋接成 blob。
3. 全程在 `RING_RELAX`(env `SEG_RING_RELAX`,預設開)toggle 後面。

### 13.3 隔離回歸(跨 5 deck / 67 slide)
- 寫 harness 比對 baseline(toggle off)vs relaxed 的 `detect_cards` 輸出。
- 第一版(只放寬 gate):改 9 張,**testtt s6 把 Model D/E 三卡串成 blob**(回歸)。
  關鍵發現:s6 假洞最弱邊 0.67 **反而 > 真卡 0.54**,證明單靠邊框強度無法分。
- 加上「弱卡不串接 + 與強卡重疊就丟」後:**8 張 slide 救回漏卡、零回歸**
  (testtt s2/s7/s8、HW6 s7/s8/s18、InfoGraphic s3/s6),且 toggle off 時與
  原始嚴格行為**位元級相同**。移除了 testtt s2 原本暫加的 `merge` override。

### 13.4 連帶修掉的 pre-existing bug:audio 時長
- 重切整個 testtt 後,composition 每張 slide 都是 **6.55s**(預設值)。
- 根因:`find_ffprobe`(`media.py` + `segment_elements.py`)只找
  `cwd/node_modules`,但 cwd 是 task dir、`node_modules` 在**上一層專案根**,
  所以 ffprobe 永遠找不到 → `audio_duration` 全部 fallback。從沒 render 過
  所以沒被發現。
- 修法:兩處 `find_ffprobe` 改成往 `cwd` 及所有上層目錄找(對齊
  `render_final_video.py` 既有寫法)。
- 修後時長正確反映真實旁白(21–33s/張),總長 **77s → 299s**。沒這修,
  render 出來每張音檔都會被截到 6.5 秒。

### 13.5 交付狀態
- 重切整個 testtt(11 slides)+ rebuild timeline/composition,
  `validate_composition.py` = OK: 11 slides, 88 layers, all faithful。
- 改動檔:`skill-.../segment_elements.py`、`skill-.../media.py`、
  `task=testtt/seg_overrides.json`(移除 s2 override);testtt output/ +
  composition.json 重生成。
- 文件:全域 `~/.claude/CLAUDE.md` 加「5. Generalize, Don't Patch」原則;
  SKILL.md override 段補回歸掃描紀律 + broken-border 範例;USER_MANUAL §14。
- **仍未 render MP4**(下一步)。workstream A(旁白驅動 timing)未動:s2 現在
  多了 Marketing/State 兩卡,進場仍按 index 平均分配。

---

## 14. 舊版第 9 點（保留作參考）

### 9.1 最自然的延續 — 切圖編輯 v3：Split
- 用途：「這一塊應該切成兩塊」。
- 工作量：UI 要讓 user 畫一條切線 / 兩個新 bbox；server 要從 `original − background` 切出兩個 layer 並 mutate metadata（或寫到 edits 目錄）。
- 收 v1+v2+v3 = 切圖編輯 UI 整條線完整（P2 #8 全部 ✅）。

### 9.2 次選 — Aspect ratio toggle（P1 #6）
- 用途：9:16 / 1:1 直式短影音輸出。
- 工作量：`canvas` 已在 project_config.json，要把「改 aspect 後重跑整條 pipeline」串起來（PDF→PNG 用新尺寸、segment 重切、composition 重建）。
- 重點：不是「後期 UI toggle」，是「pipeline 參數」，要明確告訴 user 這會重新跑很久。

### 9.3 其他可挑的
- **per-layer OCR** + UI 標紅低 confidence 切塊（解鎖切圖修正 UI 的 review 用途）
- **字幕 forced-alignment** 用 whisper-timestamped 取代字數比例分配
- **收斂 4 份 `hyperframes/animation.js`**、3 份 `task=*/scripts/` 副本到單一 skill（P3 #10）
- **HyperFrames adapter**（如果想真的接 HeyGen，第 0 節留的門）

### 9.4 還沒做但別忘了的小東西
- 上傳大 PPTX 時 fetch 沒有 progress 條（只有秒數計時器）— 想做的話要改 XMLHttpRequest。
- Bbox 編輯沒 undo — 要改要在 overrides 加歷史，或 UI 加「reset bbox」按鈕。
- 編輯後的 `task=*/output/slide_##/edits/` 目錄無 git 管控（在 .gitignore 之外），目前是「下次 segment 重跑會留著」。
