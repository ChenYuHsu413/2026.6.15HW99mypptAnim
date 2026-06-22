# 使用手冊 — Image-PPTX → 動畫旁白影片 pipeline

把「NotebookLM 出的圖片型 PPTX / 平面 PDF」自動切成 element layers、套旁白與字幕、輸出 1920×1080 / 30fps（或 9:16 / 1:1）動畫 MP4 的整套系統。

每張投影片被切成多個 layer（卡片、箭頭、標題、illustration…），按旁白時間軸逐一進場；繁中 TTS 旁白 + 燒錄字幕；UI 內可邊看邊改切塊、旁白、字幕、aspect、OCR 校對。

---

## 1. 安裝與依賴

### 必要
- **Python 3.10+**（測過 3.14）
- **Node.js**（裝 ffmpeg-static / ffprobe-static binaries）
- **LibreOffice**（PPTX → PDF 轉檔；winget `TheDocumentFoundation.LibreOffice`）

### Python 套件
```bash
pip install pymupdf rapidocr opencv-python pillow numpy edge-tts
```

### Node 套件（在 repo 根目錄）
```bash
npm install
```
這會把 `ffmpeg.exe` / `ffprobe.exe` 放到 `node_modules/ffmpeg-static/` 與 `node_modules/ffprobe-static/bin/win32/x64/`。所有 Python script 都會優先找這個位置。

### 可選
- **whisper-timestamped**（字幕 forced-alignment，把字幕時間戳對齊到實際語音）
  ```bash
  pip install whisper-timestamped
  ```
  首次跑會下載 `base` model (~140 MB)。沒裝就跳過、用字數比例分配。
- **GPU + CUDA**：whisper 用 GPU 可快 10–30 倍。沒設定就跑 CPU。

---

## 2. 整體流程

```
PDF / PPTX
  └─ (LibreOffice 轉) → PDF
     └─ render_slides.py    → output/slide_NN/original.png
        ├─ ocr_slides.py    → output/slide_NN/slide_ocr.json
        ├─ tts_edge.py      → audio/slide_NN_voiceover.mp3
        └─ segment_elements.py → output/slide_NN/{metadata.json, background.png, slide_NN_*.png}
           └─ build_timeline.py → narration/{narration_timing.json, subtitles.srt, subtitles.vtt}
              ├─ align_subtitles.py (optional, refine 字幕時間戳)
              └─ build_composition.py → composition.json
                 └─ render_final_video.py → final/final_video_with_voiceover_and_subtitles.mp4
```

**關鍵設計：三層資料流**
```
generated（metadata.json + slide_NN_*.png + slide_ocr.json + narration_timing.json）
   └─ overrides.json（user / AI 編輯只動這裡）
        └─ composition.json（resolved 合約）─┬─ render_final_video.py → MP4
                                            └─ hyperframes/ 瀏覽器預覽 / UI
```
- 生成檔永遠 pristine（自動產出）
- 編輯只寫 overrides.json
- composition.json 是 read-only 合約

---

## 3. 啟動 UI server

```bash
cd <repo root>
python pipeline_server.py          # default port 9001, localhost only
python pipeline_server.py 9001 0.0.0.0 share   # 開放 LAN（無 auth）
```

開瀏覽器：`http://localhost:9001/pipeline-ui/`

> Port 8000-8099 在某些 Windows 機器被保留卡死，改用 9001 / 9000 / 3000 / 5000 / 5500 / 9090 都可以。

---

## 4. 上傳新 deck

UI 右上 **`Upload deck`** 按鈕：
1. **Task name** — 自定義（如 `MyDeckJune`），合法字元 `A-Za-z0-9._-`
2. **File** — `.pptx` / `.ppt` / `.pdf`
3. PPTX 會自動透過 LibreOffice 轉 PDF（大 deck 10–60s，UI 顯示進度文字 + 秒數）
4. 上傳完 task 自動進 dropdown，UI 顯示 **Pending state**（沒 composition.json）

### Pending state 接下來
1. UI 中央有 **▶ Run pipeline** 按鈕 → 一鍵跑 render → ocr → tts → segment → timeline → align → composition
2. 若沒 `narration/narration_script.md` → tts 步驟 skip 並顯示 **📝 Generate starter narration & re-run** 按鈕，會用 OCR 內容生 placeholder 旁白稿、再重跑

跑完 UI 自動載入 composition、可以開始編輯。

---

## 5. UI 主要功能

### 5.1 三欄佈局
- **左側 sidebar** — slide 列表（縮圖、layer 數、duration）
- **中央 viewer** — preview composite + timeline + notes
- **右側 inspector** — slide stats / 旁白編輯 / voice 選擇 / layer 列表

### 5.2 Topbar
| 控件 | 功能 |
|---|---|
| Task dropdown | 切換 task |
| **↶ Undo** | 還原最近一次編輯（不算 notes 鍵盤輸入） |
| **Upload deck** | 上傳新 deck |
| Theme | dark / light / slate / warm |

### 5.3 Viewer header
| 控件 | 功能 |
|---|---|
| **Aspect pills (16:9 / 9:16 / 1:1)** | 切換 canvas aspect，會重跑 render→ocr→segment→timeline→composition（**會 reset overrides**，UI 警告） |
| **▶ Play slide** | 在 preview 內模擬該 slide 動畫 + 音檔 + 字幕 |
| **Open in HyperFrames** | 新分頁打開 `task=*/hyperframes/index.html` 全 deck 播放 |
| **MP4 export** | 跑 `render_final_video.py` 出 MP4（彈窗指定起訖 slide） |

### 5.4 Composite tabs
`Composite / Original / Background / Debug / Gallery` — 切換預覽資料源

---

## 6. 切圖編輯

每張 slide 右側 layer 列表，每 row 一個 layer，可點選。

### 6.1 Layer row 按鈕
| 按鈕 | 功能 |
|---|---|
| 👁 / 🙈 | Hide / Show（drop 出 composition） |
| ↑ ↓ | z-index ± 1 |
| ✂ | 進 Split mode（見 6.3） |

### 6.2 多選操作
Ctrl-click 多個 row → 上方浮現 action bar：
- **Merge** — 同進場時間、繼承 primary layer 的 animation/start（primary = 名字第一個）
- **Ungroup** — 解除 merge group
- **Hide / Show** — 批次
- **Clear** — 取消多選

### 6.3 Bbox 拖拉
點選任一 layer → preview 上該 layer 浮現黃框 + 4 corner handle + 中心 move zone：
- 拖任一 corner → resize
- 拖中心 → move
- 鬆手自動 reextract（`output/slide_##/edits/<name>` 寫新透明 PNG）+ rebuild composition

### 6.4 Split（v3，兩種 mode）
點 ✂ → 進 Split mode，上方浮現工具列：

**Cut mode（adjacent halves）**
- preview 上黃色拖把線 + 兩半 shade（青 A / 粉 B）
- 拖把線改切點
- **Vertical / Horizontal** 切換軸
- Confirm 送 `split:{axis:'x'|'y', at:0..1}`

**Bboxes mode（non-adjacent 兩 region）**
- 點工具列 **Bboxes** pill
- preview 上兩塊獨立可拖的青/粉框，各自 4 corner handles + 中心 move
- 兩 region **可重疊 / 可非相鄰**
- Confirm 送 `split:{bboxes:[[x,y,w,h], [x,y,w,h]]}`

### 6.5 Recursive split
切完的 child layer 仍可被選中再 split，無限深度。child 命名：
```
slide_02_card_01.png
  └─ slide_02_card_01_split_a.png
      └─ slide_02_card_01_split_a_split_a.png   ← 一直接下去
```

### 6.6 Child layer 也是 first-class
切完的 child 可以 hide、改 bbox、改 z、改 animation、改 OCR、再 split。

---

## 7. OCR review（切圖品管）

OCR 結果在每個 layer row 下方顯示一條「OCR 證據條」：

| 顏色 | 條件 | 含意 |
|---|---|---|
| 青色 | line_count > 0 且 conf ≥ 0.60 | 正常匹配，顯示 conf% 與 snippet |
| 紅色 ⚠ | line_count > 0 且 conf < 0.60 | OCR 不確定，請人工核對 |
| 橘色 ◑ | line_count = 0 且 type ∈ {text_block / key_point_card / annotation / table} | 該 type 通常有文字但 OCR 沒抓到，可能切塊有問題 |
| 無 | line_count = 0 且 type ∈ {arrow / illustration / icon...} | 正常（圖案類無文字） |

右側 Slide data card 會列「`Low-conf OCR: N`」、「`Missing OCR: N`」（僅有 N>0 時顯示）。

### 點 OCR 條 → 校對 modal
- 顯示完整 OCR 文字 + meta（type / conf / lines / bbox）
- Textarea 可編輯
- **Save correction** → 寫到 `overrides.json.slide_XX.layers.<name>.ocr_corrected`，composition 顯示 conf 100% + ✎ 綠色徽章
- **Reset to auto-OCR** → 清除校對，回到自動辨識結果

### Per-child re-OCR（split 後）
- 切完 child 用「中心點落 child bbox 內」歸屬，**正常 case 不跑 RapidOCR**
- 若 child 沒被歸屬到任何 line（line 中心剛好被切線切到 boundary）→ server 自動對 child PNG 跑一次 RapidOCR、cache 到 `edits/<stem>.ocr.json`、composition 優先讀 cache

---

## 8. 旁白與字幕

### 8.1 旁白編輯
右側 inspector → Narration textarea → **Save narration (re-TTS)**：
- 寫到 `overrides.json.slide_XX.narration`
- Server 觸發 `tts_edge.py` 重生該 slide 音檔（其他 slide 不動）
- 自動 rebuild timeline + composition + 字幕

### 8.2 換 voice / 語速
- **Voice select** — `HsiaoChen (女) / YunJhe (男) / HsiaoYu (女)`，可在 voice_config.json 加更多
- **Rate** — `+38%` 較快（≈1.5×）、`-8%` 慢速教學
- **Apply voice (all)** → 全 deck 重 TTS

### 8.3 字幕
- 自動由 `chunk_narration()` 切成 ≤32 字 / cue
- 燒錄到 MP4 的字幕用半透明黑底 + 1920×960 letterbox（畫面下 120px 暗條，字幕永不遮 slide）
- 兩檔同步生：`narration/subtitles.srt` + `.vtt`

### 8.4 Forced-alignment（可選）
若 `pip install whisper-timestamped`：
- `align_subtitles.py` 在 `build_timeline` 之後自動跑
- 對每個 slide MP3 跑 whisper word-level 轉錄
- 用 char→word greedy match 把每 cue 對到實際語音邊界
- 覆寫 `subtitles.srt` / `.vtt`，**cue 文字不變、僅時間戳精修**
- 沒裝就 graceful skip、保留字數比例版

模型大小換時間：
| MODEL_NAME | 大小 | 速度（CPU） | 準度 |
|---|---|---|---|
| `tiny` | 75 MB | 最快 | 最低 |
| `base`（default） | 140 MB | 中 | 夠用 |
| `small` | 466 MB | 慢 | 較準 |
| `medium` | 1.5 GB | 很慢 | 準 |

改 `skill/scripts/align_subtitles.py` 的 `MODEL_NAME` 常數即可。

---

## 9. Aspect ratio toggle

Viewer header **`16:9 / 9:16 / 1:1`** pills，按下：

1. 警告：「會 reset 所有 layer 編輯（split / bbox / merge / hide / OCR correction）」
2. 確認 → POST `/aspect`、清 `output/slide_*/` + `overrides.json` + `composition.json` + `narration_timing.json`
3. 寫新 `task=*/project_config.json` 把 canvas pin 住
4. 跑 render → ocr → segment → timeline → composition（**skip TTS**，音檔不受 aspect 影響）
5. 右下浮現 result panel 顯示各步驟 ✓ / 耗時
6. UI 自動 reload

**Preset**：
- `16:9` = 1920×1080（橫式預設）
- `9:16` = 1080×1920（Reels / Shorts）
- `1:1` = 1080×1080（IG）

**Render letterbox 行為**：`render_slides.py` 用 `min(W/page.w, H/page.h)` fit-inside-pad-white。16:9 deck 換 9:16 上下會有大片白邊；要 smart reflow 不在本系統範圍。

**單 task 耗時**：20 slides ~58 秒（writing-os 級別 ~25–60 秒）。

---

## 10. Undo

每個 `/apply` 之前，現行 `overrides.json` 會被快照到 `task=*/.overrides_history/NNNN.json`（ring buffer，cap 20）。

**Topbar ↶ Undo 按鈕**：
- 沒歷史時 disabled
- 按下 → POST `/undo` → 回復最近 snapshot
- 若 snapshot 與當前 narration / voice 有差 → 自動重跑 TTS + timeline
- 一定 rebuild composition

**例外**：notes 編輯（每 keystroke 觸發 `/apply`）**不快照**，否則 buffer 瞬間吃完。

---

## 11. MP4 export

Viewer header **MP4 export** 按鈕：
- Dialog 指定起訖 slide
- Run → 跑 `render_final_video.py`
- 輸出：`task=*/final/final_video_with_voiceover_and_subtitles.mp4`

涉及 ffmpeg（需要 `node_modules/ffmpeg-static/ffmpeg.exe`）。20 slides 約 5–15 分鐘（CRF 18 / veryfast）。

---

## 12. HyperFrames export

`skill/scripts/export_hyperframes.py`：把 composition.json 對映成「通用 timeline JSON」並寫 `hyperframes/project.hf.json`。

⚠ **目前是 speculative stub** — 沒接真實 HF schema。腳本檔頭已說明：
```bash
python export_hyperframes.py --list-fields    # 印出可映射的 18 欄位 + 7 動畫名
python export_hyperframes.py [out_path]       # 跑 export
HF_ABS_PATHS=1 python export_hyperframes.py   # 絕對路徑
```

若拿到真實 HF 匯出檔，把腳本內 `_HF_FIELD_MAP` / `_HF_ANIM_NAMES` 字典編對，刪掉 `_speculative` 標頭即可。

---

## 13. 進階：直接編 overrides.json

UI 寫不到的細節可以直接編 `task=*/overrides.json`。schema：

```json
{
  "voice": {"voice": "zh-TW-YunJheNeural", "rate": "-10%"},
  "slide_03": {
    "narration": "覆蓋這頁的旁白",
    "notes": "free-form 給 agent 看的備註",
    "layers": {
      "slide_03_chart_01.png": {
        "start": 1.2,
        "duration": 0.9,
        "animation": "zoom-in",
        "z": 6,
        "hidden": false,
        "merge_group": "g1",
        "bbox": [100, 100, 800, 600],
        "split": {"axis": "x", "at": 0.5},
        "ocr_corrected": "校對後的文字"
      }
    }
  }
}
```

| 欄位 | 用途 |
|---|---|
| `voice.voice` / `voice.rate` | 全 deck voice 設定 |
| `slide_NN.narration` | 該 slide 旁白覆蓋 |
| `slide_NN.notes` | 自由文字註解（不影響 render） |
| `slide_NN.layers.<name>.start` | layer 進場時間（秒，相對 slide 起點） |
| `.duration` | 動畫長度 |
| `.animation` | `fade-in / fade-in-down / fade-in-up / pop-in / zoom-in / wipe-in / draw-in` |
| `.z` | z-index |
| `.hidden` | true = drop |
| `.merge_group` | 同 group 字串的同步進場 |
| `.bbox` | [x, y, w, h] 改框（會自動 reextract PNG） |
| `.split.axis` + `.at` | cut-line 切（at 0.05–0.95） |
| `.split.bboxes` | 兩個獨立 child bbox |
| `.ocr_corrected` | 人工校對 OCR；null 取消 |

存檔後 UI 重 load 即套用（或 POST `/apply`）。

---

## 14. 跨 deck 重用 skill

整套已封裝成 `.claude/skills/skill-pptx-to-animated-video/`，要做新 deck：

1. 建新資料夾 `task=NewDeckName/`
2. 把 PDF / PPTX 丟進去（UI Upload 也會自動建）
3. 在 UI 跑 Pipeline，或手動：
   ```bash
   cd task=NewDeckName/
   python ../skill-pptx-to-animated-video/scripts/render_slides.py deck.pdf
   python ../skill-pptx-to-animated-video/scripts/ocr_slides.py
   # 寫 narration/narration_script.md
   python ../skill-pptx-to-animated-video/scripts/tts_edge.py
   python ../skill-pptx-to-animated-video/scripts/segment_elements.py
   python ../skill-pptx-to-animated-video/scripts/build_timeline.py
   python ../skill-pptx-to-animated-video/scripts/align_subtitles.py   # 可選
   python ../skill-pptx-to-animated-video/scripts/build_composition.py
   python ../skill-pptx-to-animated-video/scripts/render_final_video.py
   ```

不需要 copy script。所有 task 共用同一份 `skill-pptx-to-animated-video/scripts/`。

### Per-task 設定覆蓋
在 `task=*/` 放這些檔案會深合併進 skill 預設：
- `project_config.json` — canvas / render 參數
- `voice_config.json` — voice 預設
- `caption_config.json` — 字幕字型 / 顏色 / margin / chunk_max_chars
- `seg_overrides.json` — 切圖時的 per-slide 覆蓋(`merge`/`suppress`/`order`/
  `irregular`),keyed by slide 號碼。**只在通用演算法會誤判別的 deck 時才用**;
  優先改演算法,並用「跨 deck 回歸掃描」驗證(見 SKILL.md「overrides vs
  algorithm changes」)。

> **卡片邊框斷裂會自動救回**:一張卡若有 *一邊* 邊框被相鄰圖形(箭頭、漏斗)
> 蓋斷,以往會整張掉進背景;現在 `segment_elements.py` 會自動把它當卡片救回
> (其餘三邊完整即可),不需要 per-task override。可用 `SEG_RING_RELAX=0` 關閉
> 回到舊的嚴格行為。

---

## 15. 檔案結構速查

```
<repo root>/
├─ pipeline_server.py          ← 啟動這個
├─ package.json                ← npm install 後 node_modules/ffmpeg-static
├─ pipeline-ui/                ← repo-root UI（所有 task 共用）
│   ├─ index.html
│   ├─ app.js
│   └─ styles.css
├─ task-index.json             ← UI dropdown 列表（自動維護）
├─ skill-pptx-to-animated-video/
│   ├─ SKILL.md                ← 給 Claude / AI 看的 spec
│   ├─ config/
│   │   ├─ project_config.json   ← canvas 16:9 1920×1080 30fps
│   │   ├─ voice_config.json
│   │   └─ caption_config.json
│   ├─ hyperframes/            ← draft preview templates (canonical)
│   │   ├─ index.html
│   │   ├─ styles.css
│   │   └─ animation.js
│   └─ scripts/                ← 14 個 .py，所有 task 共用
│       ├─ config.py
│       ├─ render_slides.py
│       ├─ ocr_slides.py
│       ├─ tts_edge.py
│       ├─ segment_elements.py
│       ├─ build_timeline.py
│       ├─ align_subtitles.py   ★ 新增
│       ├─ build_composition.py
│       ├─ render_final_video.py
│       ├─ export_hyperframes.py ★ 新增
│       ├─ overrides.py
│       ├─ reextract.py
│       ├─ media.py
│       ├─ convert_pptx_to_pdf.py
│       └─ validate_composition.py
└─ task=MyDeck/                ← 每個 deck 一個
    ├─ MyDeck.pdf              ← 上傳的源檔
    ├─ project_config.json     ← (optional) aspect / fps 覆蓋
    ├─ overrides.json          ← 編輯只動這
    ├─ .overrides_history/     ← Undo ring buffer
    │   ├─ 0001.json
    │   └─ 0002.json
    ├─ composition.json        ← resolved 合約（自動生）
    ├─ narration/
    │   ├─ narration_script.md
    │   ├─ narration_timing.json
    │   ├─ subtitles.srt
    │   └─ subtitles.vtt
    ├─ audio/
    │   ├─ slide_01_voiceover.mp3
    │   └─ ...
    ├─ output/
    │   └─ slide_01/
    │       ├─ original.png
    │       ├─ background.png
    │       ├─ slide_01_title_01.png
    │       ├─ slide_01_table_01.png
    │       ├─ ...
    │       ├─ metadata.json
    │       ├─ slide_ocr.json
    │       └─ edits/                  ← bbox-edit / split 後的透明 PNG
    │           ├─ slide_01_table_01.png
    │           ├─ slide_01_table_01.ocr.json
    │           └─ slide_01_table_01_split_a.png
    ├─ hyperframes/             ← build_timeline 從 skill 複製
    │   ├─ index.html
    │   ├─ styles.css
    │   └─ animation.js
    └─ final/
        └─ final_video_with_voiceover_and_subtitles.mp4
```

---

## 16. Troubleshooting

| 症狀 | 解法 |
|---|---|
| Server 拒絕 bind port | Windows 8000-8099 區段被保留；用 9001 / 9000 / 3000 / 5500 |
| `ffprobe not found` | `npm install` 在 repo 根；或設 `FFMPEG_PATH` 環境變數 |
| `ffmpeg not found`（whisper） | 同上；`align_subtitles.py` 開頭會自動找 `node_modules/ffmpeg-static/` |
| PPTX 上傳 → 「Conversion failed」 | LibreOffice 沒裝，`winget install TheDocumentFoundation.LibreOffice` |
| TTS 失敗 | 需要網路（edge-tts）；或寫一份 narration 後再跑 |
| 切完字幕亂掉 | 改 `caption_config.json` 的 `chunk_max_chars`（預設 32） |
| 改 aspect 後 layer 全亂 | 是預期的：aspect 變更必然 reset overrides，需要重新切圖編輯 |
| Undo 按鈕灰著 | 該 task 還沒有 history（從 0001.json 開始計，notes 鍵盤不算） |
| Forced-alignment 沒跑 | 確認 `pip install whisper-timestamped` 且 ffmpeg 在 PATH；首次跑會下載 140 MB 模型 |
| OCR 全亂碼 | RapidOCR 預設裝 simplified 模型；確認 `Rec.lang_type: CHINESE_CHT` 已生效（`ocr_slides.py` 預設正確） |
| Bbox 拖完看不到 PNG 變 | 看 `output/slide_NN/edits/` 有沒有新檔；server log 有 `re-extracted` 行 |

---

## 17. 一句話速查

- **改一個 slide 的旁白** → UI 右側 textarea → Save
- **整 deck 換 voice** → UI 右側 voice select → Apply voice (all)
- **切錯了想重來** → ↶ Undo（最近 20 步）
- **嫌切得太碎** → Ctrl-click 多選 → Merge
- **想做 9:16** → 點 viewer 上 `9:16` pill（注意 reset overrides）
- **字幕時間不準** → `pip install whisper-timestamped` 然後重跑 pipeline
- **想看 frame-level 預覽** → `Open in HyperFrames`
- **想出片** → `MP4 export`
- **跨 deck** → 新 `task=*/` + 上傳 PDF + Run pipeline，全 task 共用同一份 skill scripts

---

`本手冊涵蓋 2026-06-20 session 6 結束的功能。詳細變更見 WORK_REPORT.md §9（split / OCR / aspect / 收斂 / Undo / per-layer start / forced-alignment / HF stub），§10 對照 ANALYSIS_REPORT 進度。`
