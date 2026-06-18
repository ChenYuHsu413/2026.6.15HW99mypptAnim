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
