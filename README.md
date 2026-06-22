# HW99 — Image-PPTX → Animated Narrated Video

把 NotebookLM 生成的「圖片型 PPTX/PDF」（每頁是一張完整圖片、沒有可編輯元件）
轉成有逐元素載入動畫、繁體中文女聲旁白、可選字幕的 1920x1080 / 30fps MP4。

整套流程已抽成可重用 Claude Code skill：`.claude/skills/skill-pptx-to-animated-video/`
（在任何專案目錄下對 Claude Code 輸入 `/skill-pptx-to-animated-video <deck.pdf>` 即可重跑同樣 pipeline）。

> 本專案的演進歷程、所有 user prompt、與設計決策的「為什麼」：
> - 流水帳：[`PROMPTS.md`](PROMPTS.md)
> - 完整 work report：[`WORK_REPORT.md`](WORK_REPORT.md)

## 主要 task

| 目錄 | Deck | 頁數 | 狀態 |
|---|---|---|---|
| `task=HW6-Startup50-Summary/` | 50_Startups_Feature_Selection.pdf | 20 | session 1 完成，完整 pipeline 已歸位 |
| `task=writing-os/` | The A-Z Writing OS.pdf | 13 | session 2-3 完成，最新影片在 `task=writing-os/final/` |
| `task=A2Z-animation/` | A2Z.pdf | 13 | 已整理完成，完整 pipeline 已歸位 |
| `task=InfoGraphic2AIGCdirection/` | The_AI_Workflow_Evolution.pdf | 10 | 完成，最早的 pipeline-ui 來源 |

每個 task folder 都是自包含的；共用邏輯都在 skill 裡。

## 安裝 / 部署

跑這套 pipeline 的機器需要 Python 套件 + 兩個系統二進位：**LibreOffice**（PPTX/PPT→PDF）與
**ffmpeg/ffprobe**（音長 + render）。**只上傳 PDF 的話不需要 LibreOffice。**

```powershell
pip install -r requirements.txt
```

LibreOffice / ffmpeg 的各 OS 安裝指令，以及「一個 `docker run` 全包、零手動安裝」的 Docker 方式，
見 [`DEPLOY.md`](DEPLOY.md)（搭配根目錄 `Dockerfile`）。

> 轉檔是在**跑 server 的那台機器**上做的——開瀏覽器的使用者什麼都不用裝。

> **session 4（2026-06-18）大改架構**：引入 `composition.json`「resolved 合約」+ `overrides.json`
> 編輯層 + config 三檔，並把渲染器與瀏覽器預覽都改讀同一份合約。新增 repo 根目錄的共用
> **pipeline-ui** 可視化編輯介面。詳見下方〈設定與編輯〉與 [`ANALYSIS_REPORT.md`](ANALYSIS_REPORT.md)。

## Pipeline

```
deck.pdf ──> output/slide_##/original.png              (render_slides.py：PDF→1920x1080 PNG)
        │
        ▼  segment_elements.py
   output/slide_##/  透明 element layers + background.png + metadata.json   ← generated（唯讀）
        │
        ▼  build_timeline.py        （讀 metadata + narration_script + overrides）
   narration/narration_timing.json + subtitles.srt/.vtt + hyperframes/ 預覽
        │
        ▼  build_composition.py      （merge：config + metadata + timing + overrides.json）
   composition.json                              ← resolved「合約」：渲染器中立的單一真相
        │                                          （validate_composition.py 驗證忠實）
        ├────────────────▼  render_final_video.py
        │           final/final_video_with_voiceover(.../_and_subtitles).mp4
        └────────────────▼  hyperframes/animation.js（瀏覽器 draft 預覽，讀同一份 composition.json）
```

**三層分離**：`generated`（metadata/narration_script，唯讀）→ `overrides.json`（使用者/AI 編輯，
只存差異）→ `composition.json`（resolved，render 前才 merge）。編輯只動 `overrides.json`，
生成來源永遠不被汙染；成片與預覽都消費同一份 `composition.json`，所以不會漂移。

旁白時間軸為主、反向安排動畫出現時間（layer 的進場時間排在該頁旁白窗口內）。

## 重新生成（task folder）

```powershell
cd task=HW6-Startup50-Summary
$SK = "../skill-pptx-to-animated-video/scripts"

# 1. 重生 TTS（語速可在 voice_config.json 設，或用 CLI 參數覆蓋：-8% 教學、+38% ≈ 1.5× 快）
python "$SK/tts_edge.py" zh-TW-HsiaoChenNeural +38%

# 2. 重切全部頁面（或指定頁碼如：python "$SK/segment_elements.py" 2 4）
python "$SK/segment_elements.py"

# 3. 依新 metadata 重建旁白時間軸 + 字幕 + 瀏覽器預覽
python "$SK/build_timeline.py"

# 4. 產生 resolved 合約 composition.json（套用 overrides.json，若有）
python "$SK/build_composition.py"

# 5. 渲染最終影片（讀 composition.json；無字幕版 + 燒錄字幕版，背景跑）
python "$SK/render_final_video.py"
```

> 只改 `overrides.json`（旁白/語音/layer 時間）時：旁白或語音改了 → 重跑步驟 1、3、4、5；
> 只改 layer 時間/動畫 → 只需步驟 4、5。用 pipeline-ui 編輯時這些會由 server 自動串起來。

只改字幕樣式時，不需要重 render，直接重燒字幕即可：
```powershell
./node_modules/ffmpeg-static/ffmpeg.exe -y -i final/final_video_with_voiceover.mp4 `
  -vf "scale=1920:960,pad=1920:1080:0:0:color=0x101010,subtitles=narration/subtitles.srt:force_style='FontName=Microsoft JhengHei,FontSize=11,PrimaryColour=&H00FFFFFF,BorderStyle=3,Outline=8,Shadow=0,BackColour=&H66000000,MarginL=30,MarginR=30,MarginV=10'" `
  -c:v libx264 -preset veryfast -crf 18 -c:a copy `
  final/final_video_with_voiceover_and_subtitles.mp4
```

## Element segmentation 邏輯（`scripts/segment_elements.py`）

每頁 slide 被當成一張圖片處理，切圖規則是和人工 review 來回校正出來的：

1. **卡片／流程方格／表格／半版面 panel**：用「被邊框包圍的內部白色區域（contour hierarchy 的洞）」偵測。
   面積上限放寬到 0.48× 投影片，允許 slide 2 那種左右兩半 panel 各自成為單一卡片。
   箭頭尖端就算畫到方格邊框上（墨水相連）也不影響；表格相鄰格自動併成一張表。
2. **箭頭／icon／獨立文字**：把卡片區域從墨水遮罩中擦掉後，再偵測剩餘連通塊，
   所以箭頭切圖不會吃到鄰格邊線。
3. **虛線箭頭**：多段不相連的小線段用「虛線鏈」規則串成一支完整箭頭。
4. **文字**：同一句話合併成一個 layer（詞距門檻隨字高縮放，大字體空格較大）。
5. **拼貼物（collage_cluster）**：3+ 個 piece 在 80px 內、有重疊軸、bbox 不超過 30%、
   raw ink ratio ≥ 0.17、無欄位走廊 → 合併為單一 illustration。
   讓 slide 2 紙堆 + REJECTED 章自動合成一塊。
6. **紅圈 highlight**：紅圈＋被圈的卡片＋紅字註記＋手繪箭頭合成一個 `highlight_group`，
   不拆散、也不誤吞旁邊的卡片（重疊區用 alpha 挖洞）。
7. **圖表上的紅色註記**（註記文字＋向量箭頭／星號）：用筆畫粗細（≥6px 半寬）和字元尺寸
   與同色的數據曲線區分，切成獨立 `annotation` layer，從圖表 crop 中塗白，圖表出現後再 fade-in。
8. **軸標籤歸圖表**：直式 y-label、x-caption、刻度數字都吸附進圖表 layer。
9. **碎片清理**：過小（<2000px²）或過細（窄邊 <26px）且緊貼卡片的殘邊併回卡片。
10. **trim/absorb 規則**：piece 邊緣與卡片重疊時，cut 必須同時 `> 14px` 且 `> 0.20×piece 對應邊`
    才合併進卡片；否則 trim 掉重疊部分。避免 footer banner 拖垮整張卡片 bbox。
11. **出場順序**：列分群（垂直中心相近為一列）→ 列內由左到右；title 永遠最先。
    兩 panel 版面自動「左 → 右」。
12. **品質門檻**：每頁所有 layers 疊回 background 必須和原圖**零像素差異**（>20 強度差才算）。

切圖過程可視化：`task=HW6-Startup50-Summary/work_preview/element_debug/slide_##_debug.jpg`（原圖／偵測框／挖空背景／重組驗證），
攤開圖：`task=HW6-Startup50-Summary/work_preview/slide_##_layer_gallery.jpg`（每層的透明 PNG、座標、出場時間）。

> **「不橫跨整張」上限**：`merge_pass` / `absorb` 任何會把框長到橫跨整張投影片（>0.78W × 0.6H）的
> 合併一律拒絕（與 `collage_cluster`/`detect_cards` 的上限同一原則）。避免密集版面（金字塔、重複元素環）
> 被併成一塊只能整片淡入的 blob。

### Per-deck 切圖微調（`seg_overrides.json`）

少數投影片若通用演算法切得不理想，可在該 task 根目錄放 `seg_overrides.json`（keyed by slide number）
做 per-deck 微調，**不必改共用演算法**（沿用切圖腳本內建的 override 機制，符合 per-task JSON 慣例）：

| 欄位 | 作用 |
|---|---|
| `merge` | 把區域內 piece 併成一塊（`tight` 拆開焊在一起的、`absorb` 調覆蓋門檻、`type` 強制分類） |
| `suppress` | 把區域內 piece 丟回背景（角落塗鴉、雜訊） |
| `order` | 指定出場順序 |
| `no_annot` | 該 chart/table 不要自動抽出紅色 annotation 層 |
| `irregular` | 切成元素**真實輪廓**（四角透明、背景只挖形狀），適合三角形/金字塔等非矩形；bbox 與鄰居重疊也不會用矩形蓋住。判定用「與紙張底色差異 → 連通填補」，連淺色平面填色也算同一區塊 |

原則仍是「優先修演算法、override 是少數例外」。格式範例與實作見 [`WORK_REPORT.md`](WORK_REPORT.md) §12.6。

## 旁白 / Voiceover

- 引擎：Microsoft Edge TTS，聲音 `zh-TW-HsiaoChenNeural`
- 預設語速：`-8%`（教學）；快版 `+38%`（≈ 1.5× 快）
- 旁白稿：`narration/narration_script.md`
- 時間軸：`narration/narration_timing.json`（每頁起迄、每個 layer 的 cue）
- 字幕：`narration/subtitles.srt` / `subtitles.vtt`，已自動分塊（每 cue ≤ 32 個 CJK 字）

要換 TTS 供應商（ElevenLabs、Azure、OpenAI、Google）：
換掉 `audio/` 內同名 MP3 後重跑步驟 2–4，各頁長度與動畫時間會自動依新音檔調整。

## 字幕排版

最終影片用 **letterbox** 模式：投影片內容縮到上方 1920x960，下方留 120px 暗條當字幕專用區。
無論 deck 底部有什麼內容（footer、alert box、子問題），字幕都不會遮到。
半透明黑底 + 白字、ASS BorderStyle=3 + BackColour=&H66000000。
`+38%` 配 32-char chunk 通常一行一句，順暢易讀。

## 設定與編輯（config / overrides / pipeline-ui）

### 全片設定（三個 config 檔）
寫死的 canvas / 語音 / 字幕設定已抽到 skill 的 `config/`（由 `scripts/config.py` 載入）：
- `project_config.json` — `canvas`(aspect/width/height/fps) + `render`(crf/preset/transition/sample_rate)
- `voice_config.json` — TTS `voice`/`rate`
- `caption_config.json` — 字幕分塊 + 燒錄 ASS 樣式 + letterbox

這些是預設值；想客製某個 deck，在該 task 根目錄放同名 JSON 即可（deep-merge 覆蓋，只寫要改的鍵）。

### 編輯層（`overrides.json`）
使用者/AI 的編輯都寫進 task 根目錄的 `overrides.json`（不動生成檔），keyed by slide：
```json
{
  "voice": { "voice": "zh-TW-YunJheNeural", "rate": "-10%" },
  "slide_03": {
    "narration": "改過的旁白文字",
    "notes": "給 agent 的備註",
    "layers": { "slide_03_chart_01.png": { "start": 1.2, "animation": "zoom-in" } }
  }
}
```
`build_composition.py` 會把它疊在 pristine metadata 上產生 `composition.json`。旁白/語音改了會連帶
重跑 TTS + timeline（音檔/字幕跟著更新）；只改 layer 時間/動畫則只重建 composition（快）。

### 預覽介面（pipeline-ui，preview-first）
repo 根目錄的 **pipeline-ui** 是給最終使用者「預覽切圖、選配音/字幕」的瀏覽器（不是編輯器；演算法
調校請見下面〈Editor mode〉）。讀 `task-index.json` + 各 task 的 `composition.json`：
```powershell
# 在 repo 根目錄
python pipeline_server.py 9001          # 預設 9001（8000-8099 在部分 Windows 機被保留擋掉）
```
開 <http://localhost:9001/>（會自動 302 → `/pipeline-ui/`）：
- **左**：slide 列表
- **中**：投影片預覽 + 預覽模式切換（`動畫預覽` / `切圖檢視`，後者用彩色 outline + 標 `數字 · type`）+ `▶ Play slide`（逐層動畫 + 同步字幕 + 語音）+ `Export MP4` 按鈕
- **右**（4 個卡片）：
  - **Slide** — 該頁基本資料（canvas、duration、layer 數、OCR 低 conf flag）
  - **Narration** — 該頁旁白文字（可改、Save 後重跑 TTS）+ 內嵌 `<audio>` 預覽
  - **Voice** — 語言下拉（zh-TW / zh-CN / en-US / ja-JP）→ 自動換 Voice 選項；語速 slider (-50%~+100%)；Apply voice (all slides)
  - **Subtitle** — on/off、字型大小、字色、底色 + 即時預覽框；Apply subtitle style → 寫進 `caption_config.json`，下次 MP4 export 自動 pick up

`/apply` 收的所有 payload（`{slide_XX: {narration}}`、`{voice: {...}}`、`{caption_style: {...}}`）都
deep-merge 進 `overrides.json` / `caption_config.json`，然後重建 composition；只有 voice/旁白變動會
重跑 TTS。

### Editor mode（給演算法作者）
所有切圖編輯能力（merge/hide/reorder、bbox 拖拉、cut-line 與雙 bbox split、OCR 校對、aspect ratio
toggle、Undo ring buffer）的**後端全保留**，可以直接寫 `overrides.json` 觸發；只有 UI 把按鈕隱藏。
詳細編輯 schema 見 [`WORK_REPORT.md`](WORK_REPORT.md) §8–§9。

> 純檢視不需 server：`python -m http.server 9001` 一樣能開（只是配音/字幕 Apply 跟 render 無效）。

### 真 HeyGen HyperFrames export（`export_hyperframes_html.py`）
把 `composition.json` 變成 HF CLI 接受的 HTML composition + GSAP timeline + assets 專案：
```powershell
cd task=writing-os
python ../skill-pptx-to-animated-video/scripts/export_hyperframes_html.py
cd hyperframes-export
npx hyperframes@0.6.93 lint       # 0 errors
npx hyperframes@0.6.93 validate   # no console errors
npx hyperframes@0.6.93 render -o out.mp4
```
output 是合格的 HF 專案（13 sub-comp + 79 assets，writing-os 上實測 → 19.7 MB MP4 / 6m 3s）。
舊版 `export_hyperframes.py`（speculative JSON）保留作參考但已不用。

## 瀏覽器預覽（單 task draft）

```powershell
cd task=HW6-Startup50-Summary
python -m http.server 9001     # 注意：8080 在部分機器被擋，用 9001/3000/5000/9000
```

開 <http://localhost:9001/hyperframes/index.html> 按 Play：背景＋透明 layers 按 **`composition.json`**
的時間軸逐個進場（與成片同源），同步播放各頁旁白 MP3。

## 輸出清單（task=HW6-Startup50-Summary/）

| 路徑 | 內容 |
|---|---|
| `task=HW6-Startup50-Summary/output/` | original.png、background.png、透明 element layers、metadata.json |
| `task=HW6-Startup50-Summary/audio/` | 20 段旁白 MP3 |
| `task=HW6-Startup50-Summary/narration/` | 旁白稿、timing JSON、SRT/VTT（已分塊） |
| `task=HW6-Startup50-Summary/hyperframes/` | index.html、styles.css、animation.js、project.json（瀏覽器預覽） |
| `task=HW6-Startup50-Summary/final/` | 無字幕版 MP4 + 燒錄字幕（letterbox）版 MP4 |
| `task=HW6-Startup50-Summary/work_preview/` | 切圖 debug 圖、layer 攤開圖、字幕檢查 frame |

## Skill 結構

```
skill-pptx-to-animated-video/
├── SKILL.md                # workflow、segmentation 品質規則、字幕規範、config/overrides 說明
├── config/                 # 全片預設（可被 task 根目錄同名檔覆蓋）
│   ├── project_config.json     # canvas + render
│   ├── voice_config.json       # TTS voice/rate
│   └── caption_config.json     # 字幕分塊 + ASS 樣式 + letterbox
└── scripts/
    ├── config.py              # 載入三個 config（skill 預設 ⊕ task 覆蓋）
    ├── overrides.py           # overrides.json 載入 + 套用 helper + 穩定 id
    ├── media.py               # 從當前音檔重新量測 duration（解耦 TTS↔切圖）
    ├── render_slides.py       # PDF → PNG
    ├── tts_edge.py            # Edge TTS（讀 effective 旁白 + voice override）
    ├── segment_elements.py    # 切圖（collage_cluster、panel-card、trim fraction）
    ├── build_timeline.py      # 字幕分塊、SRT/VTT、hyperframes 預覽（讀 overrides）
    ├── build_composition.py   # 產生 composition.json（resolved 合約，套用 overrides）
    ├── validate_composition.py# 驗證 composition 忠實對應來源
    ├── render_final_video.py  # 讀 composition.json → letterbox 字幕燒錄
    ├── export_hyperframes_html.py # composition.json → 真 HeyGen HF 專案（HTML+GSAP+assets）
    └── export_hyperframes.py  # 舊 speculative JSON stub（保留作參考，不用）

# repo 根目錄（跨 task 共用）
pipeline-ui/                # composition-driven 可視化編輯介面（瀏覽全部 task）
pipeline_server.py          # 服務 UI + /apply /render /suggest（任一 task），預設 port 9001
```

未來在任何專案要把圖片型投影片轉成旁白動畫 MP4，呼叫 `/skill-pptx-to-animated-video` 即可。
