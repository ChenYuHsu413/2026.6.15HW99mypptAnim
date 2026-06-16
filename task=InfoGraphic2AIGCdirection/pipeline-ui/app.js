const state = {
  tasks: [],
  taskPath: null,
  project: null,
  timing: null,
  slides: [],
  metadataBySlide: new Map(),
  selectedSlide: null,
  selectedLayer: null,
  view: 'composite',
  showSkippedLayers: false,
  overrides: {},
  dirty: false,
};

const projectRoot = new URL('../../', window.location.href).href;
const taskSelect = document.getElementById('taskSelect');
const saveBtn = document.getElementById('saveBtn');
const slideList = document.getElementById('slideList');
const statusEl = document.getElementById('status');
const selectedSlideLabel = document.getElementById('selectedSlideLabel');
const selectedSlideTitle = document.getElementById('selectedSlideTitle');
const showSkippedLayers = document.getElementById('showSkippedLayers');
const compositePreview = document.getElementById('compositePreview');
const assetPreview = document.getElementById('assetPreview');
const timeline = document.getElementById('timeline');
const slideStats = document.getElementById('slideStats');
const narrationText = document.getElementById('narrationText');
const audioPlayer = document.getElementById('audioPlayer');
const layerList = document.getElementById('layerList');

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

  state.overrides = {};
  state.dirty = false;
  saveBtn.disabled = true;
  saveBtn.textContent = 'Save overrides';

  try {
    const saved = await fetchJson(`${root}pipeline_state.json`);
    if (saved && typeof saved === 'object') state.overrides = saved;
  } catch (_) { }

  renderSlideList();
  selectSlide(state.slides[0]?.slide);
  statusEl.textContent = `${state.slides.length} slides loaded from ${taskPath}.`;
  saveBtn.disabled = !state.dirty;
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
    const img = document.createElement('img');
    img.className = `layer-img${isSkippedLayer(layer) ? ' skipped' : ''}`;
    img.dataset.layer = layer.name;
    img.src = `${rt}output/${key}/${layer.name}`;
    img.alt = '';
    img.style.left = `${layer.x / slide.width * 100}%`;
    img.style.top = `${layer.y / slide.height * 100}%`;
    img.style.width = `${layer.width / slide.width * 100}%`;
    img.style.height = `${layer.height / slide.height * 100}%`;
    img.style.zIndex = layer.z_index;
    compositePreview.appendChild(img);
  }
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
  saveBtn.textContent = 'Saved ✓';
});

document.querySelectorAll('.asset-tab').forEach(btn => {
  btn.addEventListener('click', () => {
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

init();
