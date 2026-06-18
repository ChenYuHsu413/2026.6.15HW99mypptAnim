# HW99 切圖→影片系統：架構分析與下一步規劃

> 本報告為純分析，撰寫當下未修改任何專案檔案。
> 依據：實際讀過 `render_slides.py`、`segment_elements.py`(1278 行)、`build_timeline.py`、
> `render_final_video.py`、`tts_edge.py`、實際輸出檔（`metadata.json` / `project.json` /
> `narration_timing.json`）、以及 `pipeline_server.py` + `pipeline-ui/app.js`。

---

## 0. 一個必須先講清楚的關鍵誤解：「HyperFrames」

規劃裡多次提到「產生 HyperFrames composition / 把 storyboard 轉成 HyperFrames 的 HTML/CSS/JS」。
但實際讀程式後：

- **真正的最終影片不是 HyperFrames 渲染的。** `render_final_video.py` 是一支**自製的 numpy + ffmpeg 合成器**，
  用 Python 重新實作了 fade/pop/zoom/wipe/draw 動畫，一幀一幀疊圖再 pipe 給 ffmpeg。
- 專案裡的 `hyperframes/` 資料夾（`index.html` / `animation.js` / `styles.css` / `project.json`）
  只是一個**瀏覽器預覽器**，讀 `project.json` 用 CSS animation 播放，跟最終 MP4 是**兩套各自實作的動畫**。
- `skills-lock.json` 裡那一堆 `heygen-com/hyperframes` skill **目前沒有被任何 render 流程實際呼叫**。

**這點很重要**，直接影響規劃：你現在其實有「一套自有的中間格式 + 自有的渲染器」。要嘛
(A) 繼續走自有渲染器（最穩、已驗證可動），要嘛 (B) 真的改接 HeyGen HyperFrames（要重寫 render，
動畫語意要對齊）。**建議先確定走哪條路**——這是整個規劃的分叉點。

---

## 1. 現有切圖邏輯如何運作

| 問題 | 實際情況 |
|---|---|
| 從哪讀 PPT/PDF？ | **只吃 PDF**。`render_slides.py` 明確拒絕非 `.pdf`，PPTX 要先用 PowerPoint 或 `soffice --headless --convert-to pdf` 轉。**目前沒有 PPTX 直通管線。** |
| 如何轉每頁圖片？ | pymupdf (`fitz`) 把每頁依比例縮放後**置中貼到白色 1920×1080 畫布** → `output/slide_##/original.png`。 |
| 如何判斷要切哪些區塊？ | OpenCV，非 AI：①卡片/表格/panel 用「邊框包圍的內部白洞」(`RETR_CCOMP` hierarchy) 偵測；②擦掉卡片後對剩餘墨水做連通塊→箭頭/icon/文字；③`collage_cluster` 把碎片合成插圖；④紅圈 highlight、紅色圖表註記特殊規則。閾值是人工 review 反覆校出來的。 |
| 切片存哪？ | `output/slide_##/`：每層一張透明 PNG（檔名 `slide_##_{type}_{nn}.png`）+ `background.png`（挖空填色的底圖）。 |
| 保留原始整頁圖？ | ✅ `original.png` 保留。 |
| 保留 bbox/座標？ | ✅ 每層 `x/y/width/height`（絕對像素）。 |
| 保留 page index？ | ✅ `metadata["slide"]`（整數）。 |
| 保留 segment id？ | ⚠️ **沒有獨立 id**，用檔名當 id。檔名 = `type_序號`，**改 type 會改檔名**，對「以 id 綁定 override」很脆弱。 |
| 判斷 segment type？ | ✅ `classify()` 輸出 10 類：`title / key_point_card / table / text_block / chart / icon / arrow / illustration / highlight_group / annotation`。 |
| debug/可視化？ | ✅ `work_preview/element_debug/slide_##_debug.jpg`（原圖/框/挖空/重組）+ `work_preview/slide_##_layer_gallery.jpg`（每層攤開）。 |
| 品質驗證 | ✅ 硬性閘門：所有層疊回 background 必須與原圖 **0 像素差異**。 |

### ⚠️ 一個規劃裡會踩到的隱藏耦合
`segment_elements.py` 在切圖時就**讀 TTS 音檔長度**（`audio_duration(slide_##_voiceover.mp3)`）
來決定 slide `duration`，並據此把每層的 `start` **均勻散佈在旁白窗口內**。也就是說：

> **TTS 必須在切圖之前跑**，而且**改語音/語速會改 duration → 每層 start 失效**。

這和規劃的「先切圖給使用者看 → 之後才選語音/語速」順序**是相反的**。不是 bug，但 UI 流程設計時
必須處理的核心約束（見第 5 節）。

---

## 2. 現有中間資料 vs 你想要的 JSON

你列的 7 個檔案，**沒有一個以那個名字存在**。實際對應如下：

| 你想要的 | 現況 | 落差 |
|---|---|---|
| `segments.json` / `regions.json` | `output/slide_##/metadata.json`（per slide） | 最接近，但**缺 id、缺 OCR text、缺 confidence** |
| `storyboard.json` | `narration/narration_timing.json` + `hyperframes/project.json` | 有時間軸與 cue，但跟 segment 資料**混在一起、又分散兩處** |
| `project_config.json` | **不存在** | 1920×1080×30fps **寫死在 4 支程式 + CSS**；無 aspect ratio 概念 |
| `caption_config.json` | **不存在** | 字幕樣式**寫死在 `render_final_video.py` 的 ffmpeg 字串**裡 |
| `voice_config.json` | **不存在** | voice/rate 只是 `tts_edge.py` 的 CLI 參數，**用完不留** |
| `layout_overrides.json` | `OVERRIDES={}`(寫死在 .py) + `pipeline_state.json`(部分) | 沒有穩定的 overrides 檔；且 server 目前**直接改寫 metadata.json** |

### metadata.json 每層實際欄位
`name, type, x, y, width, height, z_index, animation, start, duration, narration_cue`
其中 `narration_cue` **目前只是 type 字串**（例如 `"table"`），**不是真的旁白文字、也不是 OCR**。

### ⚠️ 你最需要補的 metadata（目前完全沒有）
- **`id`**：穩定、與 type/檔名脫鉤的 segment id。
- **`ocr_text`**：**整個系統沒有任何 OCR**。沒有文字內容 → UI 無法顯示/編輯每塊文字、AI 無法理解每塊
  在講什麼、title/paragraph 的區分只能靠幾何啟發。**這是最大的缺口。**
- **`confidence`**：切圖完全沒有信心分數，UI 無法「優先標紅可疑切塊」。
- **`source_type` / `text_content`**：若未來吃原生 PPTX，可直接拿到文字與物件類型，比事後 OCR 準。

### 🚨 一個目前就存在的資料安全問題
`pipeline_server.py` 的 `/apply` 一邊把 overrides 存進 `pipeline_state.json`，**一邊又直接 `write_json`
覆蓋 `metadata.json`**（patch start/duration/animation/z_index），甚至直接改 `narration_script.md`。
這**違反了 `log.md` 裡自己寫的「不要覆寫 metadata，改用 overrides 疊加」原則**——目前「使用者編輯」
和「演算法重生」會互相覆蓋，沒有乾淨的分層。**這是進入可編輯 UI 前必須先解決的架構債。**

---

## 3. 建議的資料格式（schema 提案）

核心原則 = **三層分離**（這正是 `log.md` Phase 2 的本意，但目前沒落實）：

```
generated/  ← 演算法輸出，read-only，重切就整批覆蓋
overrides/  ← 使用者/AI 的編輯，只存「差異」，永不被演算法蓋掉
resolved/   ← render 前才把兩者 merge，餵給渲染器（draft 與 final 都讀這個）
```

建議的檔案佈局（每個 task 一份）：

```jsonc
// project_config.json — 全片設定（aspect ratio 在這裡決定！）
{
  "canvas": { "aspect": "16:9", "width": 1920, "height": 1080, "fps": 30 },
  "render": { "crf": 18, "transition": 0.5 }
}

// voice_config.json
{ "engine": "edge-tts", "voice": "zh-TW-HsiaoChenNeural", "rate": "-8%", "per_slide": {} }

// caption_config.json
{ "enabled": true, "font": "Microsoft JhengHei", "size": 11,
  "position": "letterbox-bottom", "box_opacity": 0.6, "chunk_max_chars": 32 }

// segments/slide_##.json — 擴充版 metadata（粗體=新增）
{ "slide": 2, "layers": [{
    "id": "s02-e03",                // ← 穩定 id（取代用檔名當 key）
    "name": "slide_02_table_01.png",
    "type": "table",
    "bbox": [27,27,905,930],
    "z_index": 5,
    "ocr_text": "...",             // ← 新增
    "confidence": 0.0,             // ← 新增
    "suggested_animation": "fade-in-up",
    "animation": "fade-in-up", "start": 0.45, "duration": 0.7
}]}

// storyboard.json — 純時間軸，引用 segment id，不重複幾何資料
{ "slides": [{ "slide": 2, "start": 0.0, "end": 20.54,
    "cues": [{ "id": "s02-e03", "t": 0.45, "action": "fade-in-up", "caption": "..." }] }] }

// overrides.json — 只存使用者改的差異，key 用穩定 id
{ "s02-e03": { "start": 1.2, "animation": "zoom-in" },
  "captions": { "slide_02": [{ "t": 1.0, "text": "..." }] } }
```

要點：
- **aspect ratio 放 `project_config`**，且必須在「PDF→PNG」階段就決定（見第 4 節，這是現況最難改的點之一）。
- **segment 幾何（generated）與 storyboard 時間軸（可被 override）分開**——目前兩者糊在 `metadata.json`
  與 `narration_timing.json`，導致重切就洗掉使用者的時間調整。
- **overrides 用穩定 id 當 key**，不要用檔名（檔名綁 type，不穩）。

---

## 4. HyperFrames / 渲染流程分析

**哪些參數最終影響成片**（依現況自製渲染器）：

| 參數 | 目前在哪決定 | 想做成 UI 可調的難度 |
|---|---|---|
| 影片比例 (16:9 / 9:16 / 1:1) | **PDF→PNG 階段就寫死 1920×1080**，之後 segmentation 座標、CSS、ffmpeg 全部跟著 | 🔴 **高**。改比例 = 重算畫布 → 重切圖（座標全變）→ 重渲染。**不可能當作後期 UI 即時切換**，必須當「重跑 pipeline 的參數」 |
| 每層動畫 | `segment_elements.py` 的 `ANIMATION` 字典（type→動畫），server 可 patch | 🟢 低，已有 override 路徑 |
| 字幕位置/字體/底色 | **寫死在 `render_final_video.py` ffmpeg 字串**（letterbox 到上方 960px + ASS style） | 🟡 中。要抽成 `caption_config` 再組字串 |
| 語音/語速 | `tts_edge.py` CLI 參數，**不持久化** | 🟡 中。要落成 `voice_config` |
| 字幕時間戳 | `build_timeline.py` 依**字數比例**分配（非 forced-align） | 🟡 中，精度有限（已是已知限制） |
| 動畫強度 | **目前不存在這個概念**（位移量 22/24px 等是寫死常數） | 🟡 要新增參數 |

**draft preview vs final render 應該怎麼分：**
- **draft = `hyperframes/index.html`**（瀏覽器 CSS 動畫，即時、零成本）。已經存在，很好。
- **final = `render_final_video.py`**（ffmpeg，慢，幾分鐘）。
- 兩者**現在是各自實作動畫**，存在「預覽跟成片不一致」的風險。理想是兩者都讀同一份 `resolved`
  storyboard，動畫語意對齊。

**若要真的改接 HeyGen HyperFrames**：`storyboard.json`(resolved) 是正確的中間層，把它轉成
HyperFrames composition 即可——但要先讀 `skills-lock.json` 那些 hyperframes skill 的實際 composition
schema 再做對應。**建議先不要**，先把自有渲染器的中間格式整理乾淨，HyperFrames 當第二階段選項。

---

## 5. 你的 9 步 UI 流程：可行性評估

| 你的步驟 | 在現況的可行性 |
|---|---|
| 1. 上傳 PPT/PDF | 🟡 PDF 可；**PPTX 要先補轉檔步驟**（soffice/LibreOffice） |
| 2. 後端轉圖+切圖 | 🟢 已有，但**切圖目前依賴 TTS 先跑**（耦合，見下） |
| 3. 顯示切圖結果 | 🟢 `pipeline-ui` 已經能做（layer 列表、highlight、gallery、debug、play） |
| 4. 使用者修正切圖 | 🔴 **最弱**。目前只能調 start/duration/animation/順序；**無法 UI 上合併/拆分/拉 bbox**——切錯只能改 `OVERRIDES`(改 .py) 或跟 agent 對話。真正的「切圖修正 UI」幾乎還沒做 |
| 5. 調字幕/語音/語速/比例 | 🟡 語音/語速有關鍵字 hack；**字幕樣式、影片比例沒有 UI、且比例改不動**（見第 4 節） |
| 6. AI 產生/修正 storyboard | 🟡 `/suggest` 有，但只是規則啟發（合併同行、z 序、節奏），**非真 AI 理解內容**（因為沒 OCR） |
| 7. 確認 storyboard | 🟢 可看 timeline |
| 8. 產生 composition | 🟡 = build_timeline 產 project.json（非真 HyperFrames，見第 0 節） |
| 9-10. draft/final | 🟢 預覽 + ffmpeg 都有 |

**整體判斷**：流程 **70% 的骨架已經存在**，但有三個**順序性/架構性**障礙會讓它無法照所畫順序直接組起來：

1. **TTS 必須先於切圖**（duration 反向驅動 start）→「先切圖、後選語音」要嘛接受「改語音後重算時間軸」，
   要嘛把 start 的計算從 segmentation 移到 timeline 階段。
2. **aspect ratio 在最前面就被烘死** → 不能當後期 toggle。
3. **沒有乾淨的 generated/overrides 分層** → 使用者編輯會被重切洗掉。

---

## 6. 優點（值得保留的部分）

1. **切圖演算法本身很扎實**：10 類 type + 0 像素重組驗證 + 一堆從真實 review 內化的規則
   （panel-card 0.48 上限、collage ink-ratio 0.17、trim/absorb fraction rule）。
   這是專案最有價值的資產，**不要重寫**。
2. **「旁白時間軸為主、反推動畫進場」** 的設計方向正確。
3. **draft（瀏覽器）/ final（ffmpeg）雙軌** 概念對。
4. **debug 可視化**（debug.jpg + layer gallery）非常適合接到「切圖檢查 UI」，幾乎是現成素材。
5. **pipeline-ui 已有 task 切換、layer highlight、逐 slide 播放+字幕+語音同步**，是很好的 read-only 底座。
6. **letterbox 字幕方案**（壓到上 960px）能保證字幕不擋內容，是務實的好解法。

---

## 7. 不足 / 需重構的部分

**建議重構（架構債）：**
1. 🔴 **引入 generated/overrides/resolved 三層**，停止 server 直接覆寫 `metadata.json` / `narration_script.md`。
2. 🔴 **把「全片設定」從程式碼抽成 config 檔**（aspect/fps/字幕樣式/語音）——目前散在 4 支 .py + CSS + ffmpeg 字串。
3. 🟡 **把 per-layer `start` 的計算從 `segment_elements.py` 移到 `build_timeline.py`**，讓切圖與配音解耦
   （切圖只管幾何，時間軸只管時間）。
4. 🟡 **給每個 segment 穩定 `id`**，override 改用 id 綁定。

**建議補功能：**
5. **OCR**（最大缺口）：補 `ocr_text` → 解鎖真 AI storyboard、文字編輯、可靠 type 分類。
6. **confidence 分數** → UI 標紅可疑切塊。
7. **UI 上的切圖修正**（合併/拆分/調 bbox），而非只能改 .py 或對話。

**可保留 / 不要動：**
- `segment_elements.py` 的偵測核心、0px 驗證、debug 可視化、draft 預覽器、letterbox 字幕。

**未來可能要重構（但非現在）：**
- 自製渲染器 vs HeyGen HyperFrames 的取捨（第 0 節）。
- 字幕從字數比例 → forced alignment（whisper-timestamped）。
- **3 份漂移的 script copies**（writing-os / A2Z / HW6 各有一份 `scripts/`，HW6 甚至檔名都不同）
  vs canonical skill——目前 `pipeline_server` 只呼叫 skill 版，task 內副本是死的，建議統一。

---

## 8. 建議開發優先順序

```
P0（地基，不做後面都會返工）
  1. 決定渲染路線：自製渲染器 vs HyperFrames（第 0 節）
  2. 抽出 project_config / voice_config / caption_config 三個 config 檔
  3. 建立 generated/overrides/resolved 三層；server 停止覆寫 metadata
  4. 給 segment 穩定 id

P1（解開順序耦合，讓 UI 流程能照順序跑）
  5. 把 per-layer start 計算移到 build_timeline（切圖↔配音解耦）
  6. aspect ratio 設計成「重跑 pipeline 參數」，UI 上明確標示改它要重切

P2（解鎖智慧編輯）
  7. 加 OCR → ocr_text + confidence
  8. UI 切圖修正（合併/拆分/bbox），寫進 overrides 而非 metadata

P3（打磨）
  9. 字幕 forced-alignment、PPTX 直通、HyperFrames 接口（若 P0 選了它）
 10. 收斂 3 份 script 副本到單一 skill
```

---

## 9. 如果要進入實作，下一步先做什麼

**建議的單一第一步（最小、最高槓桿、可驗證）：**

> **做 P0 的第 1+2 步：先確認渲染路線，然後把「寫死的全片設定」抽成 `project_config.json` /
> `voice_config.json` / `caption_config.json`，並讓現有 4 支 script 改讀這三個檔
> （行為完全不變、輸出影片 byte 不變即為通過）。**

理由：這一步**不碰切圖演算法、不碰 UI、風險最低**，但它是後面所有「UI 可調參數」的前提——
沒有 config 層，UI 根本沒有東西可以綁。而且它有明確的成功判準（重構前後產出的 MP4 應一致）。

---

## 需要拍板的兩個決策

動工前，有兩個方向會根本性影響架構：

1. **渲染路線**：繼續用現有自製 ffmpeg 渲染器，還是真的改接 HeyGen HyperFrames？
   （影響第 0、4、8 節整條路線）→ **✅ 已拍板：先走 A，見第 10 節。**
2. **PPTX 範圍**：第一版只支援 PDF（沿用現況），還是一定要 PPTX 直通？
   （要加 LibreOffice 轉檔，且可考慮直接讀 PPTX 物件拿到文字/座標，繞過 OCR）
   → **✅ 已拍板：範圍 2（UI 接受 .pptx、後端自動轉 PDF），見第 11 節。**

---

## 10. 渲染架構決策：方案 A + 可替換渲染合約

> 討論後拍板（取代第 0 節的「待選擇」狀態）。

### 10.0 先釐清一個關鍵誤會：切圖 ≠ HyperFrames

- **切圖（segmentation）從頭到尾是自製的 `segment_elements.py`（純 OpenCV），跟 HyperFrames 零關係。**
  HyperFrames 是「渲染/合成」階段的工具，不做切圖。
- `skill-pptx-to-animated-video` 是**本專案自己做出來的 skill**，四步（PDF→PNG、切圖、build timeline、
  自製渲染）沒有一步呼叫 HyperFrames。
- 專案裡的 hyperframes 蹤跡都不是主線：①`task=*/hyperframes/` 只是 `build_timeline.py` 產的瀏覽器預覽頁
  （借名，非 HeyGen 產品）；②`.agents/skills/hyperframes-*` 是官方 skill 裝著但沒被渲染流程呼叫；
  ③`my-video/` 是一個空的 HeyGen 腳手架（`npx hyperframes@0.6.93`），沒產出過任何正式影片。
- **結論：4 支影片 100% 由自製渲染器 `render_final_video.py` 產生；HyperFrames 從未在本專案上線。**

### 10.1 決定

**現階段走方案 A（沿用自製 numpy+ffmpeg 渲染器），但把「渲染段」設計成可替換介面，替未來接 HyperFrames 留門。**

理由：現在 70% 的待辦（切圖修正 UI、config 分層、OCR、generated/overrides 三層）**不管選 A 或 B 都得做、
且都與渲染器無關**。先換渲染器＝地基沒好就先換屋頂。而「可替換」幾乎是免費的——它跟 P0 的 resolved 層
是同一份東西。

### 10.2 可替換點 = 一份「渲染器中立的合約」

在「資料」與「渲染器」之間放一份單一文件 `composition.json`（= resolved 層本人）。上游只負責產出它，
下游任何渲染器只負責消費它。

```
 切圖 + 時間軸 + config + overrides ──▶ composition.json (合約)
                                              │  ← 可替換點
                       ┌──────────────────────┼──────────────────────┐
                       ▼                       ▼                      ▼
               adapter_custom          adapter_browser        adapter_hyperframes
            (現 numpy/ffmpeg=A)        (即時 draft 預覽)        (未來才寫=B)
                       ▼                       ▼                      ▼
                   final.mp4                draft 預覽              final.mp4
```

合約草案（**只描述「要呈現什麼」，不出現任何 ffmpeg/CSS 方言**）：

```jsonc
{
  "canvas": { "aspect": "16:9", "width": 1920, "height": 1080, "fps": 30 },
  "slides": [{
    "index": 2,
    "background": "output/slide_02/background.png",
    "duration": 20.54,
    "audio": "audio/slide_02_voiceover.mp3",
    "layers": [{
      "id": "s02-e03",
      "image": "output/slide_02/slide_02_table_01.png",
      "bbox": [27, 27, 905, 930],
      "z": 5,
      "enter": { "type": "fade-up", "intensity": 0.6 },  // ← 抽象動畫名 + 強度，不是CSS也不是numpy
      "start": 0.45, "duration": 0.7
    }],
    "captions": [{ "t": 1.0, "end": 4.2, "text": "..." }]
  }],
  "caption_style": { "$ref": "caption_config.json" },
  "transition": { "type": "crossfade", "duration": 0.5 }
}
```

### 10.3 必須守住的設計紀律

1. **動畫用「語意名稱 + 強度(0–1)」表達**（`fade-up` / `zoom` / `wipe` / `highlight`…），
   絕不寫成 `translateY(24px)` 或某個 ffmpeg filter。UI 的「動畫強度」滑桿就是調 `intensity`，
   每個 adapter 自己把語意對到它的實作。
2. **動畫語彙是一個「封閉清單」，每個 adapter 都必須實作清單裡的每一個**；缺哪個就明確報錯，
   不可默默 fallback（否則 draft 與 final 又會漂移）。
3. **合約裡不可洩漏任何渲染器方言**（例如 `subtitles=...force_style='...'` 這種 ffmpeg 字串一旦進來，
   可替換性就破功）。
4. **不要過度設計**：可替換的正確份量 = 一份有文件的 JSON schema + 一個薄 dispatch
   （`render(composition, renderer="custom")`）。不需要 plugin 系統 / 抽象基底類別。
   現在只有一個渲染器，合約 + 一個 adapter 就夠；HyperFrames adapter 等真的要做時再加。

### 10.4 draft 預覽要走同一份合約嗎？→ 要

- **同一份合約（同資料、同動畫語意、同時間軸）：要。** draft 就是 `adapter_browser`，與成片讀同一份
  `composition.json`。這正是用來根除「目前預覽(CSS)與成片(numpy)是兩套各自實作、會漂移」的問題。
- **同一個渲染引擎：不必、也不該。** 預覽要即時便宜（CSS 夠），成片要高品質（numpy/ffmpeg）。
  **合約管「what」，adapter 管「how」**；draft 不需與成片像素一致，只要忠實到能做編輯決定即可。
- Bonus：未來換成 HyperFrames 時，draft 與 final 甚至可變成同一引擎（HyperFrames preview vs render），
  漂移直接消失——反證「合約放在這條邊界」是對的。

### 10.5 對現有程式的影響（評估，未實作）

- `render_final_video.py` 約八成程式（疊圖、緩動、ffmpeg pipe、字幕燒錄）**照舊不動**；
  唯一改的是「入口」：從「自己翻好幾個檔」改成「讀一份 `composition.json`」。等於換一個讀資料的前門 = adapter。
- 與報告 P0 完全重疊：`canvas`→`project_config`、resolved 層→`composition.json`、layer `id`、
  `caption_config`/`voice_config` 引用。**把 resolved 層設計成這份中立合約，可替換性就自動成立。**

### 10.6 一句話結論

> **把 resolved 層設計成一份渲染器中立的 `composition.json`，現有渲染器改成讀它的第一個 adapter；
> draft 預覽走同一份合約、但用自己的便宜引擎。** HyperFrames 的門就此留著，代價幾乎為零。

---

## 11. 輸入格式決策：PPTX 範圍（範圍 2）

> 討論後拍板。前提確認：實際要處理的主要是 **NotebookLM 圖片型簡報**（每頁一張平面圖、無可編輯元件）。

### 11.1 釐清「為什麼變成只讀 PDF」（查 `PROMPTS.md` 歷史）

- 最初目標（Session 1 #2）字面上是「圖片型 PPTX → MP4」，但同句已註明來源 deck 是 NotebookLM、
  **每頁都是平面圖、沒有可編輯元件**，要求「把每頁當圖片處理」。
- Session 1 #16、Session 2 #1 顯示：skill 成形時輸入**已是 .pdf**，且是使用者自己問「能不能吃 PDF」。
- 真相：**native PPTX 解析從未被實作過**，因為測試 deck 沒有可解析的元件。把投影片變成乾淨
  1920×1080 圖片最可靠的工具是 pymupdf（吃 PDF），所以路徑固化成 **PPTX →(匯出）PDF → PNG → 切圖**。
- 結論：**不是「PPTX 支援被拿掉」，而是它本來就只走「光柵化成圖片」這條路，PDF 只是最乾淨的中繼站。**

### 11.2 關鍵區分：兩種 PPTX

| | (a) 圖片型 PPTX（本專案實況） | (b) 原生可編輯 PPTX |
|---|---|---|
| 每頁 | 一張平面大圖，無元件 | 有 text box / shape / chart，各自獨立 |
| 讀 native 能拿到 | 幾乎沒有 | 文字 + 精確座標 + 元件類型（現成） |
| 還要切圖嗎 | 要（只能 OpenCV） | 不用 |
| 還要 OCR 嗎 | 要 | 不用 |

→ Q2 的本質不是「吃 .pptx 或 .pdf」，而是「**要不要把原生可編輯 PPTX 當一等公民路徑**」。

### 11.3 決定：範圍 2

**UI 接受 `.pptx`，後端自動轉 PDF（`soffice --headless --convert-to pdf`），引擎完全不變。**

- 成本低：只在 pipeline 最前面加一步轉檔；後面 PPTX→PDF→PNG→切圖 全部照舊。
- 從使用者視角「PPTX 為主」成立，且不碰任何已驗證的切圖/渲染核心。
- 適用本專案所有 deck（皆圖片型）。

### 11.4 不做範圍 3（原生 PPTX 快速路徑）——列為未來分支

- 範圍 3＝用 `python-pptx` 偵測每頁是「單張全幅圖」還是「真有元件」，原生型直接讀元件、
  **跳過切圖與 OCR**。回報很高，但**只有當真的會餵原生 PowerPoint 進來時才有意義**。
- 既然輸入主要是 NotebookLM 圖片型，範圍 3 對現有 deck 零幫助，**先不為尚未出現的輸入造分支**。
- 兩者不衝突：未來做範圍 3 時，範圍 2 的「PPTX→PDF→切圖」正好變成「偵測為圖片型」的 fallback，
  現在做範圍 2 不會白做。

### 11.5 影響

- 新增依賴：LibreOffice（`soffice`）作為轉檔器（僅在輸入是 .pptx 時才需要）。
- `render_slides.py` 仍只認 PDF；轉檔在它之前發生（UI/後端那一層），保持核心單純。

---

## 附錄：關鍵檔案位置速查

| 用途 | 路徑 |
|---|---|
| PDF→PNG | `skill-pptx-to-animated-video/scripts/render_slides.py` |
| 切圖核心（1278 行） | `skill-pptx-to-animated-video/scripts/segment_elements.py` |
| 時間軸 + 字幕 + 預覽器產生 | `skill-pptx-to-animated-video/scripts/build_timeline.py` |
| 最終 MP4 渲染（自製合成器） | `skill-pptx-to-animated-video/scripts/render_final_video.py` |
| TTS | `skill-pptx-to-animated-video/scripts/tts_edge.py` |
| 每頁切圖輸出 | `task=*/output/slide_##/metadata.json` + 透明 PNG + `background.png` |
| 全片彙整（預覽用） | `task=*/hyperframes/project.json` |
| 時間軸 | `task=*/narration/narration_timing.json` |
| 旁白原稿 | `task=*/narration/narration_script.md` |
| 唯一有 UI 的 task | `task=InfoGraphic2AIGCdirection/`（`pipeline_server.py` + `pipeline-ui/`） |
| 切圖規則文件 | `skill-pptx-to-animated-video/SKILL.md` |
