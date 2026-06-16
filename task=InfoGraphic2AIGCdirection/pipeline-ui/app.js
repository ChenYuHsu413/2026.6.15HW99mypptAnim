const state = {
  tasks: [],
  taskPath: null,
  project: null,
  timing: null,
  slides: [],
  metadataBySlide: new Map(),
  subtitleMap: {},
  selectedSlide: null,
  selectedLayer: null,
  view: 'composite',
  showSkippedLayers: false,
  overrides: {},
  dirty: false,
};

const projectRoot = new URL('../../', window.location.href).href;
const taskSelect = document.getElementById('taskSelect');
const themeSelect = document.getElementById('themeSelect');
const saveBtn = document.getElementById('saveBtn');
const applyBtn = document.getElementById('applyBtn');
const pipelineLog = document.getElementById('pipelineLog');
const slideList = document.getElementById('slideList');
const statusEl = document.getElementById('status');
const selectedSlideLabel = document.getElementById('selectedSlideLabel');
const selectedSlideTitle = document.getElementById('selectedSlideTitle');
const showSkippedLayers = document.getElementById('showSkippedLayers');
const compositePreview = document.getElementById('compositePreview');
const assetPreview = document.getElementById('assetPreview');
const timeline = document.getElementById('timeline');
const suggestBtn = document.getElementById('suggestBtn');
const suggestionsPanel = document.getElementById('suggestionsPanel');
const suggestionsList = document.getElementById('suggestionsList');
const playBtn = document.getElementById('playBtn');
const hyperframesBtn = document.getElementById('hyperframesBtn');
let playTimers = [];
const slideStats = document.getElementById('slideStats');
const narrationText = document.getElementById('narrationText');
const audioPlayer = document.getElementById('audioPlayer');
const layerList = document.getElementById('layerList');
const adjustmentNotes = document.getElementById('adjustmentNotes');

const pad = v => String(v).padStart(2, '0');
const slideKey = n => `slide_${pad(n)}`;
const fmt = v => Number(v ?? 0).toFixed(2);
const ANIMATIONS = ['fade-in-down', 'fade-in-up', 'fade-in', 'pop-in', 'zoom-in', 'wipe-in', 'draw-in'];

async function fetchJson(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

function taskRoot(taskPath) {
  return new URL(`${taskPath}/`, projectRoot).href;
}

function currentTaskFromUrl() {
  const m = window.location.pathname.match(/\/(task=[^/]+)\/pipeline-ui\//);
  return m ? m[1] : null;
}

function setTaskSelect(tasks, sel) {
  taskSelect.innerHTML = '';
  for (const t of tasks) {
    const opt = document.createElement('option');
    opt.value = t.path;
    opt.textContent = t.label || t.path;
    taskSelect.appendChild(opt);
  }
  if (sel) taskSelect.value = sel;
}

function ovKey(slideNum) { return slideKey(slideNum); }

function slideOverride(slideNum) {
  const k = ovKey(slideNum);
  if (!state.overrides[k]) state.overrides[k] = { narration: null, layers: {} };
  return state.overrides[k];
}

function layerOverride(slideNum, layerName) {
  const so = slideOverride(slideNum);
  if (!so.layers[layerName]) so.layers[layerName] = {};
  return so.layers[layerName];
}

function markDirty() {
  state.dirty = true;
  saveBtn.disabled = false;
  applyBtn.disabled = false;
  saveBtn.textContent = 'Save overrides';
}

async function loadTask(taskPath) {
  const root = taskRoot(taskPath);
  const [project, timing] = await Promise.all([
    fetchJson(`${root}hyperframes/project.json`),
    fetchJson(`${root}narration/narration_timing.json`),
  ]);

  state.taskPath = taskPath;
  state.project = project;
  state.timing = timing;
  state.slides = project.slides || [];
  state.metadataBySlide = new Map();

  await Promise.all(state.slides.map(async slide => {
    const meta = await fetchJson(`${root}output/${slideKey(slide.slide)}/metadata.json`);
    state.metadataBySlide.set(slide.slide, meta);
  }));

  // Parse subtitles
  state.subtitleMap = {};
  try {
    const srtText = await (await fetch(`${root}narration/subtitles.srt`)).text();
    const currentCues = [];
    let globalIdx = 0;
    for (const block of srtText.split(/\n\n+/)) {
      const lines = block.trim().split('\n');
      if (lines.length < 3) continue;
      const timeMatch = lines[1].match(/([\d:,]+)\s*-->\s*([\d:,]+)/);
      if (!timeMatch) continue;
      const parseSrtTime = t => { const p = t.split(/[:,]/).map(Number); return p[0]*3600 + p[1]*60 + p[2] + p[3]/1000; };
      const start = parseSrtTime(timeMatch[1]);
      const end = parseSrtTime(timeMatch[2]);
      const text = lines.slice(2).join('\n');
      // Assign cue to a slide based on global cue index
      currentCues.push({ start, end, text, globalIdx });
      globalIdx++;
    }
    // Distribute cues across slides
    let cueIdx = 0;
    for (const slide of state.slides) {
      const key = slideKey(slide.slide);
      const slideStart = state.timing[key]?.start ?? 0;
      const slideEnd = state.timing[key]?.end ?? 999;
      const cues = [];
      while (cueIdx < currentCues.length && currentCues[cueIdx].end <= slideEnd + 0.1) {
        const c = currentCues[cueIdx];
        cues.push({ start: c.start - slideStart, end: c.end - slideStart, text: c.text });
        cueIdx++;
      }
      state.subtitleMap[key] = cues;
    }
  } catch (_) { }

  state.overrides = {};
  state.dirty = false;
  saveBtn.disabled = true;
  applyBtn.disabled = true;
  saveBtn.textContent = 'Save overrides';
  pipelineLog.textContent = 'Apply edits and click "Apply to pipeline" to execute.';

  try {
    const saved = await fetchJson(`${root}pipeline_state.json`);
    if (saved && typeof saved === 'object') state.overrides = saved;
  } catch (_) { }

  renderSlideList();
  selectSlide(state.slides[0]?.slide);
  statusEl.textContent = `${state.slides.length} slides loaded from ${taskPath}.`;
  saveBtn.disabled = !state.dirty;
  applyBtn.disabled = !state.dirty;
  applyBtn.disabled = !state.dirty;
}

async function init() {
  try {
    state.tasks = await fetchJson(`${projectRoot}task-index.json`);
    if (!Array.isArray(state.tasks) || !state.tasks.length) throw new Error('task-index.json is empty');
    const sel = state.tasks.some(t => t.path === currentTaskFromUrl()) ? currentTaskFromUrl() : state.tasks[0].path;
    setTaskSelect(state.tasks, sel);
    await loadTask(sel);
  } catch (e) {
    console.error(e);
    document.body.innerHTML = document.getElementById('loadErrorTemplate').innerHTML;
  }
}

function activeLayers(slide) {
  const meta = state.metadataBySlide.get(slide.slide) || slide;
  return (meta.layers || []).filter(l => state.showSkippedLayers || !isSkippedLayer(l));
}

function isSkippedLayer(l) { return l.type === 'key_point_card'; }

function renderSlideList() {
  slideList.innerHTML = '';
  for (const slide of state.slides) {
    const meta = state.metadataBySlide.get(slide.slide) || slide;
    const timing = state.timing[slideKey(slide.slide)] || {};
    const skipped = (meta.layers || []).filter(isSkippedLayer).length;
    const so = state.overrides[slideKey(slide.slide)];
    const hasOvr = so && (so.narration || Object.keys(so.layers || {}).length > 0);
    const btn = document.createElement('button');
    btn.className = `slide-button${hasOvr ? ' has-override' : ''}`;
    btn.dataset.slide = slide.slide;
    btn.innerHTML = `
      <strong>Slide ${pad(slide.slide)}${hasOvr ? ' *' : ''}</strong>
      <span>${fmt(slide.duration)}s, ${meta.layers.length} layers${skipped ? `, ${skipped} skipped` : ''}</span>
      <span>${timing.voiceover_file || 'no audio'}</span>
    `;
    btn.addEventListener('click', () => selectSlide(slide.slide));
    slideList.appendChild(btn);
  }
}

function selectSlide(num) {
  const slide = state.slides.find(s => s.slide === num);
  if (!slide) return;
  stopPlayback();
  state.selectedSlide = slide;
  state.selectedLayer = null;
  document.querySelectorAll('.slide-button').forEach(b => b.classList.toggle('active', Number(b.dataset.slide) === num));
  renderSelectedSlide();
}

function renderSelectedSlide() {
  const slide = state.selectedSlide;
  const meta = state.metadataBySlide.get(slide.slide) || slide;
  const timing = state.timing[slideKey(slide.slide)] || {};
  const layers = activeLayers(slide);
  const skipped = (meta.layers || []).filter(isSkippedLayer).length;
  const so = state.overrides[slideKey(slide.slide)];
  const hasOvr = so && (so.narration || Object.keys(so.layers || {}).length > 0);

  selectedSlideLabel.textContent = `${slideKey(slide.slide)} / editor`;
  selectedSlideTitle.textContent = `${layers.length} visible layers, ${skipped} skipped card layers${hasOvr ? ' — has overrides' : ''}`;
  renderStats(slide, meta, timing, layers, skipped);
  renderNarration(slide, timing);
  renderPreview(slide, layers);
  renderTimeline(slide, meta, timing);
  renderLayerList(slide, meta);

  // Load adjustment notes
  const sk = slideKey(slide.slide);
  const savedNote = state.overrides[sk]?.notes ?? '';
  adjustmentNotes.value = savedNote;
}

function renderStats(slide, meta, timing, layers, skipped) {
  slideStats.innerHTML = `
    <dt>Canvas</dt><dd>${slide.width} x ${slide.height}</dd>
    <dt>Duration</dt><dd>${fmt(slide.duration)}s</dd>
    <dt>Global start</dt><dd>${fmt(timing.start)}s</dd>
    <dt>Global end</dt><dd>${fmt(timing.end)}s</dd>
    <dt>Visible layers</dt><dd>${layers.length}</dd>
    <dt>Skipped cards</dt><dd>${skipped}</dd>
    <dt>Cues</dt><dd>${(timing.cues || []).length}</dd>
    <dt>Audio</dt><dd>${timing.voiceover_file ? 'yes' : 'no'}</dd>
  `;
}

function renderNarration(slide, timing) {
  const key = slideKey(slide.slide);
  const so = state.overrides[key];
  const val = so?.narration ?? timing.script ?? '';
  narrationText.innerHTML = `<textarea id="narrationTextarea" class="${so?.narration != null ? 'dirty-narration' : ''}">${escapeHtml(val)}</textarea>`;
  if (timing.voiceover_file) {
    audioPlayer.src = `${taskRoot(state.taskPath)}${timing.voiceover_file}`;
    audioPlayer.hidden = false;
  } else {
    audioPlayer.removeAttribute('src');
    audioPlayer.hidden = true;
  }
  const ta = document.getElementById('narrationTextarea');
  ta.addEventListener('input', () => {
    const s = slideOverride(slide.slide);
    const orig = timing.script ?? '';
    if (ta.value !== orig) {
      s.narration = ta.value;
      ta.classList.add('dirty-narration');
    } else {
      delete s.narration;
      ta.classList.remove('dirty-narration');
    }
    markDirty();
  });
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

function renderPreview(slide, layers) {
  const key = slideKey(slide.slide);
  const rt = taskRoot(state.taskPath);
  const assetMap = {
    original: `${rt}output/${key}/original.png`,
    background: `${rt}output/${key}/background.png`,
    debug: `${rt}work_preview/element_debug/${key}_debug.jpg`,
    gallery: `${rt}work_preview/${key}_layer_gallery.jpg`,
  };

  if (state.view !== 'composite') {
    compositePreview.style.display = 'none';
    assetPreview.style.display = 'block';
    assetPreview.src = assetMap[state.view];
    return;
  }

  assetPreview.style.display = 'none';
  compositePreview.style.display = 'block';
  compositePreview.innerHTML = '';

  const bg = document.createElement('img');
  bg.className = 'stage-bg';
  bg.src = `${rt}output/${key}/background.png`;
  bg.alt = '';
  compositePreview.appendChild(bg);

  for (const layer of layers) {
    const lo = state.overrides[slideKey(slide.slide)]?.layers?.[layer.name] || {};
    const anim = lo.animation || layer.animation || 'fade-in';
    const dur = lo.duration || layer.duration || 0.7;
    const img = document.createElement('img');
    img.className = `layer-img ${anim}${isSkippedLayer(layer) ? ' skipped' : ''}`;
    img.dataset.layer = layer.name;
    img.dataset.start = lo.start ?? layer.start;
    img.style.setProperty('--anim-duration', `${dur}s`);
    img.src = `${rt}output/${key}/${layer.name}`;
    img.alt = '';
    img.style.left = `${layer.x / slide.width * 100}%`;
    img.style.top = `${layer.y / slide.height * 100}%`;
    img.style.width = `${layer.width / slide.width * 100}%`;
    img.style.height = `${layer.height / slide.height * 100}%`;
    img.style.zIndex = layer.z_index;
    compositePreview.appendChild(img);
  }

  // Subtitle overlay
  const sub = document.createElement('div');
  sub.className = 'subtitle-overlay';
  sub.id = 'subtitleOverlay';
  compositePreview.appendChild(sub);
}

function renderTimeline(slide, meta, timing) {
  const layers = meta.layers || [];
  const dur = Math.max(slide.duration, 0.1);
  const cueMap = new Map((timing.cues || []).map(c => [c.layer, c]));
  timeline.innerHTML = '';
  for (const layer of layers) {
    const lo = state.overrides[slideKey(slide.slide)]?.layers?.[layer.name] || {};
    const start = lo.start ?? layer.start;
    const duration = lo.duration ?? layer.duration;
    const startPct = Math.max(0, Math.min(100, start / dur * 100));
    const widthPct = Math.max(0.5, Math.min(100 - startPct, duration / dur * 100));
    const cue = cueMap.get(layer.name);
    const cuePct = cue ? Math.max(0, Math.min(100, (cue.time - timing.start) / dur * 100)) : null;
    const row = document.createElement('div');
    row.className = 'timeline-row';
    row.innerHTML = `
      <span>${layer.type}</span>
      <div class="track">
        <span class="bar${isSkippedLayer(layer) ? ' skipped' : ''}" style="left:${startPct}%;width:${widthPct}%"></span>
        ${cuePct === null ? '' : `<span class="cue-dot" style="left:${cuePct}%"></span>`}
      </div>
      <span>${fmt(start)}s</span>
    `;
    timeline.appendChild(row);
  }
}

function renderLayerList(slide, meta) {
  const layers = meta.layers || [];
  layerList.innerHTML = '';
  for (const layer of layers) {
    const lo = state.overrides[slideKey(slide.slide)]?.layers?.[layer.name] || {};
    const startVal = lo.start ?? layer.start;
    const durVal = lo.duration ?? layer.duration;
    const animVal = lo.animation ?? layer.animation;
    const hasOvr = lo.start != null || lo.duration != null || lo.animation != null;

    const item = document.createElement('div');
    item.className = `layer-item${isSkippedLayer(layer) ? ' skipped' : ''}${hasOvr ? ' layer-editing' : ''}`;
    item.dataset.layer = layer.name;
    item.innerHTML = `
      <div class="layer-head">
        <span class="layer-name" title="${layer.name}">${layer.name}</span>
        <span class="pill${isSkippedLayer(layer) ? ' skipped' : ''}">${isSkippedLayer(layer) ? 'skipped' : layer.type}</span>
      </div>
      <div class="layer-meta">
        <span>z ${layer.z_index}</span>
        <span>x ${layer.x}, y ${layer.y}</span>
        <span>${layer.width} x ${layer.height}</span>
      </div>
      <div class="layer-edits">
        <label>Start <input type="number" class="edit-start" step="0.05" min="0" max="${fmt(slide.duration)}" value="${fmt(startVal)}"></label>
        <label>Duration <input type="number" class="edit-dur" step="0.05" min="0.1" max="${fmt(slide.duration)}" value="${fmt(durVal)}"></label>
        <label>Anim <select class="edit-anim">${ANIMATIONS.map(a => `<option value="${a}"${a === animVal ? ' selected' : ''}>${a}</option>`).join('')}</select></label>
      </div>
    `;
    item.addEventListener('click', () => selectLayer(layer.name));

    const startInp = item.querySelector('.edit-start');
    const durInp = item.querySelector('.edit-dur');
    const animSel = item.querySelector('.edit-anim');

    function applyLayerEdit() {
      const o = layerOverride(slide.slide, layer.name);
      const ns = parseFloat(startInp.value);
      const nd = parseFloat(durInp.value);
      const na = animSel.value;
      if (ns !== layer.start) o.start = ns; else delete o.start;
      if (nd !== layer.duration) o.duration = nd; else delete o.duration;
      if (na !== layer.animation) o.animation = na; else delete o.animation;
      item.classList.toggle('layer-editing', o.start != null || o.duration != null || o.animation != null);
      markDirty();
      renderTimeline(slide, meta, state.timing[slideKey(slide.slide)] || {});
    }

    startInp.addEventListener('input', applyLayerEdit);
    durInp.addEventListener('input', applyLayerEdit);
    animSel.addEventListener('change', applyLayerEdit);

    layerList.appendChild(item);
  }
}

function selectLayer(name) {
  state.selectedLayer = name;
  document.querySelectorAll('.layer-item').forEach(el => el.classList.toggle('active', el.dataset.layer === name));
  document.querySelectorAll('.layer-img').forEach(el => el.classList.toggle('highlighted', el.dataset.layer === name));
}

saveBtn.addEventListener('click', () => {
  const blob = new Blob([JSON.stringify(state.overrides, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'pipeline_state.json';
  a.click();
  URL.revokeObjectURL(a.href);
  state.dirty = false;
  saveBtn.disabled = true;
  applyBtn.disabled = false; // overrides still in memory, user can still apply
  saveBtn.textContent = 'Saved ✓';
});

applyBtn.addEventListener('click', async () => {
  const payload = JSON.stringify(state.overrides);
  applyBtn.disabled = true;
  applyBtn.classList.add('running');
  applyBtn.textContent = 'Running…';
  pipelineLog.textContent = 'Sending overrides to pipeline server…\n';

  try {
    const res = await fetch('http://localhost:8001/apply', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: payload,
    });
    const data = await res.json();
    if (data.status === 'ok') {
      pipelineLog.textContent = (data.logs || []).join('\n') + '\n\n✓ Pipeline applied successfully.';
    } else {
      pipelineLog.textContent = `Error: ${data.message || 'unknown'}`;
    }
  } catch (err) {
    pipelineLog.textContent = `Failed to connect to pipeline server.\n\nStart it in the task directory:\n  python pipeline_server.py\n\nError: ${err.message}`;
  } finally {
    applyBtn.disabled = false;
    applyBtn.classList.remove('running');
    applyBtn.textContent = 'Apply to pipeline';
  }
});

suggestBtn.addEventListener('click', async () => {
  if (!state.selectedSlide) return;
  suggestBtn.disabled = true;
  suggestBtn.textContent = 'Analyzing…';
  suggestionsPanel.hidden = false;
  suggestionsList.innerHTML = '<div class="suggestion-item">Running analysis…</div>';

  try {
    const res = await fetch('http://localhost:8001/suggest', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ slide: state.selectedSlide.slide }),
    });
    const data = await res.json();
    suggestionsList.innerHTML = '';
    if (data.status === 'ok' && data.suggestions?.length) {
      for (const s of data.suggestions) {
        const icons = { merge_text: '🔗', reorder: '↕️', spread: '⏱', subdivide: '✂️', consolidate: '📦', error: '⚠️' };
        const el = document.createElement('div');
        el.className = 'suggestion-item';
        el.innerHTML = `
          <span class="s-icon">${icons[s.type] || '💡'}</span>
          <div class="s-body">
            <div class="s-type">${s.type}</div>
            <div>${s.message}</div>
          </div>
          ${s.layers?.length ? '<button class="s-apply">Apply</button>' : ''}
        `;
        const applyBtn_ = el.querySelector('.s-apply');
        if (applyBtn_) {
          applyBtn_.addEventListener('click', () => {
            applyBtn_.disabled = true;
            applyBtn_.textContent = '✓';
          });
        }
        suggestionsList.appendChild(el);
      }
    } else {
      suggestionsList.innerHTML = '<div class="suggestion-item">No suggestions for this slide.</div>';
    }
  } catch (err) {
    suggestionsList.innerHTML = `<div class="suggestion-item">⚠️ Could not reach pipeline server.\nStart: <code>python pipeline_server.py 8001</code></div>`;
  } finally {
    suggestBtn.disabled = false;
    suggestBtn.textContent = 'Analyze slide';
  }
});

document.querySelectorAll('.asset-tab').forEach(btn => {
  btn.addEventListener('click', () => {
    stopPlayback();
    state.view = btn.dataset.view;
    document.querySelectorAll('.asset-tab').forEach(t => t.classList.toggle('active', t === btn));
    if (state.selectedSlide) renderPreview(state.selectedSlide, activeLayers(state.selectedSlide));
  });
});

taskSelect.addEventListener('change', async e => {
  taskSelect.disabled = true;
  try { await loadTask(e.target.value); } finally { taskSelect.disabled = false; }
});

showSkippedLayers.addEventListener('change', e => {
  state.showSkippedLayers = e.target.checked;
  if (state.selectedSlide) renderSelectedSlide();
});

// ── Play / Reset slide animation ────────────────────────────────────
function stopPlayback() {
  clearTimers();
  compositePreview.classList.remove('playing');
  document.querySelectorAll('.stage-preview .layer-img').forEach(el => el.classList.remove('show'));
  audioPlayer.pause();
  audioPlayer.currentTime = 0;
  playBtn.textContent = '▶ Play slide';
  playBtn.classList.remove('playing');
}

playBtn.addEventListener('click', () => {
  const slide = state.selectedSlide;
  if (!slide) return;
  const isPlaying = compositePreview.classList.contains('playing');
  if (isPlaying) { stopPlayback(); return; }

  // Start playback
  clearTimers();
  playTimers = [];
  document.querySelectorAll('.stage-preview .layer-img').forEach(el => el.classList.remove('show'));
  const subEl = document.getElementById('subtitleOverlay');
  if (subEl) subEl.classList.remove('show');

  compositePreview.classList.add('playing');
  playBtn.textContent = '■ Reset';
  playBtn.classList.add('playing');

  const layers = Array.from(document.querySelectorAll('.stage-preview .layer-img'));
  layers.forEach(el => {
    const start = parseFloat(el.dataset.start);
    const timer = setTimeout(() => el.classList.add('show'), start * 1000);
    playTimers.push(timer);
  });

  // Subtitle cues
  const cues = state.subtitleMap[slideKey(slide.slide)] || [];
  for (const cue of cues) {
    const showTimer = setTimeout(() => {
      if (subEl) { subEl.textContent = cue.text; subEl.classList.add('show'); }
    }, cue.start * 1000);
    playTimers.push(showTimer);
    const hideTimer = setTimeout(() => {
      if (subEl) subEl.classList.remove('show');
    }, cue.end * 1000);
    playTimers.push(hideTimer);
  }

  // Play narration audio
  const timing = state.timing[slideKey(slide.slide)];
  if (timing?.voiceover_file) {
    audioPlayer.currentTime = 0;
    audioPlayer.play().catch(() => {});
  }

  // Auto-stop at end of slide
  const endTimer = setTimeout(() => {
    compositePreview.classList.remove('playing');
    if (subEl) subEl.classList.remove('show');
    playBtn.textContent = '▶ Play slide';
    playBtn.classList.remove('playing');
  }, slide.duration * 1000 + 500);
  playTimers.push(endTimer);
});

function clearTimers() {
  playTimers.forEach(clearTimeout);
  playTimers = [];
}

adjustmentNotes.addEventListener('input', () => {
  const sk = slideKey(state.selectedSlide?.slide);
  if (!sk) return;
  const s = slideOverride(state.selectedSlide.slide);
  if (adjustmentNotes.value.trim()) {
    s.notes = adjustmentNotes.value;
  } else {
    delete s.notes;
  }
  markDirty();
});

hyperframesBtn.addEventListener('click', () => {
  const path = state.taskPath
    ? `${taskRoot(state.taskPath)}hyperframes/index.html`
    : '../hyperframes/index.html';
  window.open(path, '_blank');
});

// ── Theme ──────────────────────────────────────────────────────────
const savedTheme = localStorage.getItem('pipeline-ui-theme') || 'dark';
document.documentElement.className = `theme-${savedTheme}`;
themeSelect.value = savedTheme;
themeSelect.addEventListener('change', () => {
  const theme = themeSelect.value;
  document.documentElement.className = `theme-${theme}`;
  localStorage.setItem('pipeline-ui-theme', theme);
});

init();
