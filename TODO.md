# TODO — 剩餘待辦

> 最後更新:2026-06-22(Session 9 結束)。完整脈絡見 `WORK_REPORT.md` §13、
> 舊待辦見 `SESSION_HANDOFF.md`(部分已過時)。

## Skill 級工作(使用者明確要的,非 per-task 補丁)

### A — 旁白驅動的動畫 timing(**核心**,最大改動)
目前 `segment_elements.py` 把 layer 進場**按 index 平均分配**
(`start = 0.45 + i*cue_gap`);`narration_cue` 只是 layer 型別,不是真旁白文字。
使用者要的是「一句旁白呈現一個動畫」——每個元素在旁白講到它時才進場,而非平均「瞎放」。
需要:把 layer→旁白 cue 對應起來(by order 或語意),再由 `build_timeline.py` 從
cue 時間軸推進場時間。

### C — 欄位感知閱讀順序(generalize)
slide 7 目前用 `task=testtt/seg_overrides.json` 的 per-task `order` override
(column-major:title→左欄→中→右→banner)。使用者要這變成**通用 skill 邏輯**
(自動偵測欄位、column-aware reading order),拿掉 per-task override。
> 做法提醒:照 SKILL.md「overrides vs algorithm changes」,先量測根因、用跨 deck
> 回歸掃描驗證,只有當只影響目標 slide 才 ship。

## testtt-specific
- [ ] **Render testtt MP4** — composition 已是最新且 render-ready(時長正確、卡片
      已救回),只差輸出。UI → testtt → preview → Export,或跑 `render_final_video.py`。
- [ ] **Slide 7 reading order** — 見上面 C;目前靠 per-task `order` override 撐著。

### 已完成 / 已定案(勿重做)
- [x] **Slide 2 箭頭變大** — Session 9 用**通用演算法**解決(broken-border 卡片
      救回),Marketing Spend + State 已成正式卡;非 override。
- [x] **Slide 2 機器** — 定案:留在背景。CV 無法乾淨切出「只有機器」,通用「保留大
      密集連通塊」規則會誤抓別的 deck 背景(如 slide-6 corkboard)。除非 per-deck
      override 或上游簡報重畫(加間隙),否則別再重試。

## 其他 backlog(較低優先,來自舊 WORK_REPORT)
- [ ] per-layer OCR + UI 標紅低 confidence 切塊
- [ ] 字幕 forced-alignment(whisper-timestamped 取代字數比例分配)
- [ ] 收斂 4 份 `hyperframes/animation.js` + 3 份 `task=*/scripts/` 副本到單一 skill
- [ ] Aspect ratio toggle(9:16 / 1:1 直式短影音)
- [ ] Bbox 編輯 undo
- [ ] 上傳大 PPTX 的 fetch progress 條
