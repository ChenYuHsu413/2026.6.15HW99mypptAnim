const pad=n=>String(n).padStart(2,'0'),fmt=v=>Number(v??0).toFixed(2),sk=n=>`slide_${pad(n)}`;
const esc=s=>String(s??'').replace(/[<>&"']/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));
const LOW_CONF=0.60;  // OCR confidence threshold for the "suspicious" flag
const TEXT_TYPES=new Set(['text_block','key_point_card','annotation','table']);  // expect OCR text

const $=id=>document.getElementById(id);
const taskSelect=$('taskSelect'),themeSelect=$('themeSelect'),
  taskTitle=$('taskTitle'),statusEl=$('status'),
  slideList=$('slideList'),slideLabel=$('slideLabel'),
  slideTitleEl=$('slideTitle'),
  preview=$('preview'),assetPreview=$('assetPreview'),
  playBtn=$('playBtn'),renderBtn=$('renderBtn'),
  timelineEl=$('timeline'),statsEl=$('stats'),
  narrationEl=$('narration'),audioEl=$('audio'),saveNarration=$('saveNarration'),
  voiceLang=$('voiceLang'),voiceSel=$('voiceSel'),
  rateSlider=$('rateSlider'),rateLabel=$('rateLabel'),voiceApply=$('voiceApply'),
  subOn=$('subOn'),subSize=$('subSize'),subSizeLabel=$('subSizeLabel'),
  subFg=$('subFg'),subBg=$('subBg'),subPreview=$('subPreview'),subApply=$('subApply'),
  modePills=$('previewModePills'),notesEl=$('notes'),
  renderDialog=$('renderDialog'),renderFrom=$('renderFrom'),
  renderTo=$('renderTo'),renderGo=$('renderGo'),
  renderClose=$('renderClose'),renderLog=$('renderLog'),
  uploadBtn=$('uploadBtn'),deleteTaskBtn=$('deleteTaskBtn'),uploadDialog=$('uploadDialog'),
  upTaskName=$('upTaskName'),upFile=$('upFile'),
  upGo=$('upGo'),upClose=$('upClose'),upLog=$('upLog');
// Editor-only nodes (kept as null so old code paths short-circuit safely).
const showSkipped=null,hfBtn=null,layerEdit=null,leName=null,leStart=null,
  leDur=null,leAnim=null,leApply=null,layerList=null,undoBtn=null;

// Voice catalogue (edge-tts). Slim curated list per language — full catalogue
// is ~400 voices, this picks the natural-sounding defaults users actually want.
const VOICES={
  'zh-TW':[
    ['zh-TW-HsiaoChenNeural','曉臻 (female)'],
    ['zh-TW-HsiaoYuNeural','曉雨 (female)'],
    ['zh-TW-YunJheNeural','雲哲 (male)'],
  ],
  'zh-CN':[
    ['zh-CN-XiaoxiaoNeural','晓晓 (female)'],
    ['zh-CN-YunxiNeural','云希 (male)'],
    ['zh-CN-YunyangNeural','云扬 (male)'],
  ],
  'en-US':[
    ['en-US-AvaNeural','Ava (female)'],
    ['en-US-AndrewNeural','Andrew (male)'],
    ['en-US-EmmaNeural','Emma (female)'],
    ['en-US-BrianNeural','Brian (male)'],
  ],
  'ja-JP':[
    ['ja-JP-NanamiNeural','Nanami (female)'],
    ['ja-JP-KeitaNeural','Keita (male)'],
  ],
};
function populateVoices(lang, selectedVoice){
  const list=VOICES[lang]||VOICES['zh-TW'];
  voiceSel.innerHTML='';
  for(const [id,label] of list){
    const o=document.createElement('option');o.value=id;o.textContent=label;voiceSel.appendChild(o);
  }
  if(selectedVoice && list.some(v=>v[0]===selectedVoice)) voiceSel.value=selectedVoice;
}

const S={tasks:[],taskPath:null,canvas:{width:1920,height:1080},timing:null,slides:[],meta:new Map,
  subMap:{},selected:null,view:'composite',showSkipped:true,playing:false,timers:[],selLayer:null,rev:0,
  multiSel:new Set(),splitMode:null,historyDepth:0,
  previewMode:'animation',voice:'zh-TW-HsiaoChenNeural',rate:'+38%',captionStyle:null};
const GROUP_COLORS=['#f59e0b','#10b981','#3b82f6','#ec4899','#a855f7','#14b8a6','#f97316','#84cc16'];
function groupColor(g){if(!g)return '';let h=0;for(const c of g)h=(h*31+c.charCodeAt(0))%GROUP_COLORS.length;return GROUP_COLORS[h];}
const root=new URL('../',window.location.href).href;
function tRoot(p){return new URL(`${p}/`,root).href;}
async function fj(p){const r=await fetch(p,{cache:'no-store'});if(!r.ok)throw Error(`${r.status}`);return r.json();}

// ── Load (driven by composition.json -- the resolved contract) ───────
async function loadTask(tp){
  const r=tRoot(tp);
  let comp;
  try{ comp=await fj(`${r}composition.json`); }
  catch(_){ return showPendingTask(tp); }
  S.taskPath=tp;S.canvas=comp.canvas;
  S.slides=comp.slides.map(s=>({
    slide:s.index,width:comp.canvas.width,height:comp.canvas.height,duration:s.duration,
    layers:(s.layers||[]).map(l=>({
      name:l.image.split('/').pop(),type:l.type,
      x:l.bbox[0],y:l.bbox[1],width:l.bbox[2],height:l.bbox[3],
      z_index:l.z,animation:l.enter.type,start:l.start,duration:l.duration,
      hidden:!!l.hidden, merge_group:l.merge_group||'', ocr:l.ocr||null,
    })),
  }));
  S.meta=new Map(S.slides.map(s=>[s.slide,s]));
  S.timing={};
  for(const s of comp.slides) S.timing[sk(s.index)]={script:s.narration,voiceover_file:s.audio,start:s.start,end:s.end};
  S.subMap={};
  try{
    // SRT files are CRLF on Windows; normalise then split on blank lines.
    const srtRaw=await(await fetch(`${r}narration/subtitles.srt`,{cache:'no-store'})).text();
    const srt=srtRaw.replace(/\r\n?/g,'\n');
    const cues=[];
    for(const b of srt.split(/\n{2,}/)){
      const ln=b.trim().split('\n');if(ln.length<3)continue;
      const m=ln[1].match(/([\d:,]+)\s*-->\s*([\d:,]+)/);if(!m)continue;
      const pt=t=>{const p=t.split(/[:,]/).map(Number);return p[0]*3600+p[1]*60+p[2]+p[3]/1000;};
      cues.push({start:pt(m[1]),end:pt(m[2]),text:ln.slice(2).join('\n')});
    }
    let ci=0;
    for(const s of S.slides){
      const k=sk(s.slide),ss=S.timing[k]?.start??0,se=S.timing[k]?.end??999,cs=[];
      while(ci<cues.length&&cues[ci].end<=se+.1){const c=cues[ci];cs.push({start:c.start-ss,end:c.end-ss,text:c.text});ci++;}
      S.subMap[k]=cs;
    }
  }catch(_){}
  taskTitle.textContent=S.tasks.find(t=>t.path===tp)?.label||tp;
  renderSlideList();selectSlide(S.slides[0]?.slide);
  statusEl.textContent=`${S.slides.length} slides loaded.`;
  renderTo.value=S.slides.length;
  syncAspectPills();
  refreshHistoryDepth();
}

// ── Aspect ratio toggle (rebuilds slides at new dimensions) ─────────
function currentAspect(){
  if(S.canvas?.aspect) return S.canvas.aspect;
  const w=S.canvas?.width,h=S.canvas?.height;
  if(!w||!h) return null;
  if(w===h) return '1:1';
  return w>h ? '16:9' : '9:16';
}
function syncAspectPills(){
  const a=currentAspect();
  document.querySelectorAll('.aspect-pill').forEach(b=>b.classList.toggle('active', b.dataset.aspect===a));
}
document.querySelectorAll('.aspect-pill').forEach(btn=>{
  btn.addEventListener('click',()=>{
    if(!S.taskPath) return;
    const target=btn.dataset.aspect;
    if(target===currentAspect()) return;
    confirmAspect(target);
  });
});
function confirmAspect(target){
  const a=currentAspect()||'?';
  const ok=confirm(
    `Rebuild this task at ${target}?\n\n`+
    `• Current: ${a}\n`+
    `• Slides re-render at ${target}.\n`+
    `• OCR + segmentation re-run (1–3 min).\n`+
    `• Audio/narration untouched.\n\n`+
    `Layer edits (split/bbox/merge/hide/OCR corrections) will be reset — `+
    `bbox coordinates are aspect-specific and would be wrong at the new size.`
  );
  if(!ok) return;
  runAspectChange(target);
}
async function runAspectChange(target){
  document.querySelectorAll('.aspect-pill').forEach(b=>b.disabled=true);
  statusEl.textContent=`Rebuilding at ${target}…`;
  const t0=Date.now();
  let timer=setInterval(()=>{
    statusEl.textContent=`Rebuilding at ${target}… ${Math.floor((Date.now()-t0)/1000)}s`;
  },500);
  try{
    const r=await fetch('/aspect',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({task:S.taskPath, aspect:target})});
    const d=await r.json().catch(()=>({status:'error',message:'bad response'}));
    clearInterval(timer);
    if(d.status!=='ok'){statusEl.textContent='Aspect change failed: '+(d.message||'');return;}
    const failed=d.steps.find(s=>s.status==='error');
    showAspectResultPanel(target, d.steps);
    if(failed) statusEl.textContent=`Aspect change halted at ${failed.step}.`;
    else { statusEl.textContent=`Rebuilt at ${target} ✓`; await loadTask(S.taskPath); }
  }catch(e){
    clearInterval(timer);
    statusEl.textContent='Aspect change failed: '+e.message;
  }finally{
    document.querySelectorAll('.aspect-pill').forEach(b=>b.disabled=false);
  }
}
function showAspectResultPanel(target, steps){
  // Reuse the pending-state result container if present, else a transient div.
  let host=document.getElementById('runPipelineResult');
  if(!host){
    host=document.createElement('div');host.id='aspectResult';
    host.style.cssText='position:fixed;right:20px;bottom:20px;max-width:420px;max-height:60vh;overflow:auto;'+
      'padding:12px;background:var(--panel);border:1px solid var(--accent);border-radius:10px;z-index:500;'+
      'box-shadow:0 12px 32px rgba(0,0,0,.5);font-size:12px';
    document.body.appendChild(host);
    setTimeout(()=>host.remove(), 15000);
  }
  const icon=s=>s==='ok'?'✓':s==='skipped'?'·':'✗';
  const color=s=>s==='ok'?'#10b981':s==='skipped'?'#9ca3af':'#fb7185';
  host.innerHTML=`<div style="font-weight:700;margin-bottom:6px">Aspect → ${target}</div>`+
    steps.map(s=>
      `<div style="margin:4px 0;padding:5px 8px;background:rgba(0,0,0,.25);border-left:3px solid ${color(s.status)};border-radius:4px">`+
      `<div style="color:${color(s.status)};font-weight:600">${icon(s.status)} ${s.step}`+
      `${s.elapsed!=null?` <span style="opacity:.7;font-weight:400">(${s.elapsed}s)</span>`:''}</div>`+
      (s.log?`<pre style="margin:3px 0 0;font-size:11px;white-space:pre-wrap;opacity:.85;max-height:80px;overflow:auto">${esc(s.log)}</pre>`:'')+
      `</div>`
    ).join('');
}

function showPendingTask(tp, pdfHint){
  S.taskPath=tp;S.slides=[];S.meta=new Map();S.timing={};S.subMap={};S.selected=null;
  taskTitle.textContent=S.tasks.find(t=>t.path===tp)?.label||tp;
  slideList.innerHTML='';
  slideLabel.textContent='Pending';
  slideTitleEl.textContent='Pipeline not yet run for this task';
  statsEl.innerHTML='';narrationEl.value='';if(layerList)layerList.innerHTML='';timelineEl.innerHTML='';
  if(layerEdit)layerEdit.hidden=true;audioEl.removeAttribute('src');audioEl.hidden=true;
  const pdf=pdfHint? pdfHint.split('/').pop() : '<YOUR_DECK>.pdf';
  preview.innerHTML=
    `<div style="padding:24px;line-height:1.6;color:var(--muted)">`+
    `<h3 style="margin-top:0">No composition.json yet for <code>${tp}</code></h3>`+
    `<p>The deck file landed. Click below to run the segmentation/TTS/timeline pipeline.`+
    ` For a fresh deck you'll usually need to drop a <code>narration/narration_script.md</code>`+
    ` into <code>${tp}/narration/</code> first — the pipeline will tell you if it's missing.</p>`+
    `<div style="margin:14px 0"><button id="runPipelineBtn" class="play-btn">▶ Run pipeline</button>`+
    ` <span id="runPipelineStatus" style="margin-left:10px"></span></div>`+
    `<div id="runPipelineResult" style="font-size:12px"></div>`+
    `<details style="margin-top:18px"><summary>Or run manually in a terminal</summary>`+
    `<pre style="background:rgba(0,0,0,.3);padding:10px;border-radius:6px;overflow:auto;margin-top:8px">`+
    `python ../skill-pptx-to-animated-video/scripts/render_slides.py ${pdf}\n`+
    `python ../skill-pptx-to-animated-video/scripts/tts_edge.py\n`+
    `python ../skill-pptx-to-animated-video/scripts/segment_elements.py\n`+
    `python ../skill-pptx-to-animated-video/scripts/build_timeline.py\n`+
    `python ../skill-pptx-to-animated-video/scripts/build_composition.py</pre></details>`+
    `</div>`;
  assetPreview.style.display='none';preview.style.display='block';
  statusEl.textContent='Pending — pipeline not run';
  document.getElementById('runPipelineBtn').addEventListener('click',()=>runPipeline(tp));
}

async function runPipeline(tp){
  const btn=document.getElementById('runPipelineBtn'),st=document.getElementById('runPipelineStatus'),
    res=document.getElementById('runPipelineResult');
  btn.disabled=true;res.innerHTML='';
  const t0=Date.now();
  const timer=setInterval(()=>{st.textContent=`Running… ${Math.floor((Date.now()-t0)/1000)}s elapsed (pipeline can take 1–3 min)`;},500);
  st.textContent='Running… 0s elapsed';
  try{
    const r=await fetch('/pipeline',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task:tp})});
    const d=await r.json().catch(()=>({status:'error',message:'bad response'}));
    clearInterval(timer);
    if(d.status!=='ok'){st.textContent='Pipeline failed: '+(d.message||'');btn.disabled=false;return;}
    const icon=s=>s==='ok'?'✓':s==='skipped'?'·':'✗';
    const color=s=>s==='ok'?'#10b981':s==='skipped'?'#9ca3af':'#fb7185';
    res.innerHTML=d.steps.map(s=>
      `<div style="margin:6px 0;padding:6px 10px;background:rgba(0,0,0,.25);border-left:3px solid ${color(s.status)};border-radius:4px">`+
      `<div style="color:${color(s.status)};font-weight:600">${icon(s.status)} ${s.step}${s.elapsed!=null?` <span style="opacity:.7;font-weight:400">(${s.elapsed}s)</span>`:''}</div>`+
      (s.log?`<pre style="margin:4px 0 0;font-size:11px;white-space:pre-wrap;opacity:.85">${s.log.replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]))}</pre>`:'')+
      `</div>`
    ).join('');
    const allOk=d.steps.every(s=>s.status==='ok');
    const narrMissing=d.steps.some(s=>s.step==='tts_edge.py'&&s.status==='skipped'&&/narration_script\.md missing/.test(s.log||''));
    if(allOk){st.textContent='✓ Done — loading composition…';await loadTask(tp);}
    else if(narrMissing){
      st.textContent='Finished with issues — narration missing';
      res.insertAdjacentHTML('beforeend',
        `<div style="margin-top:12px;padding:10px;background:rgba(16,185,129,.1);border-radius:6px">`+
        `<button id="starterNarrBtn" class="play-btn">📝 Generate starter narration & re-run</button>`+
        ` <span style="margin-left:10px;font-size:11px;opacity:.8">Creates placeholder text per slide so the pipeline completes. Edit real narration in the right-side editor afterward.</span>`+
        `</div>`);
      document.getElementById('starterNarrBtn').addEventListener('click',async()=>{
        const b=document.getElementById('starterNarrBtn');b.disabled=true;b.textContent='Creating…';
        try{
          const sr=await fetch('/starter-narration',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task:tp})});
          const sd=await sr.json();
          if(sd.status!=='ok'){b.textContent='Failed: '+(sd.message||'');b.disabled=false;return;}
          await runPipeline(tp);
        }catch(e){b.textContent='Failed: '+e.message;b.disabled=false;}
      });
    }
    else st.textContent='Finished with issues — see steps below';
  }catch(e){clearInterval(timer);st.textContent='Failed: '+e.message;}
  btn.disabled=false;
}

async function refreshTaskIndex(){
  S.tasks=await fj(`${root}task-index.json`);
  const cur=taskSelect.value;
  taskSelect.innerHTML='';
  S.tasks.forEach(t=>{const o=document.createElement('option');o.value=t.path;o.textContent=t.label||t.path;taskSelect.appendChild(o);});
  if(cur && S.tasks.some(t=>t.path===cur)) taskSelect.value=cur;
}

async function init(){
  try{
    // Voice picker: seed from default language before any selection.
    if(voiceLang){ populateVoices(voiceLang.value, S.voice); }
    if(rateLabel){ rateLabel.textContent = S.rate; rateSlider.value = parseInt(S.rate,10)||38; }
    if(subPreview){ updateSubPreview(); }
    S.tasks=await fj(`${root}task-index.json`);
    taskSelect.innerHTML='';S.tasks.forEach(t=>{const o=document.createElement('option');o.value=t.path;o.textContent=t.label||t.path;taskSelect.appendChild(o);});
    const cur=S.tasks.find(t=>window.location.pathname.includes(t.path));
    await loadTask(cur?.path||S.tasks[0].path);
  }catch(e){document.body.innerHTML=`<div style="color:#fb7185;padding:40px">${e.message}</div>`;}
}

// ── Apply a structured edit -> server -> reload (WYSIWYG) ─────────────
async function applyEdit(overrides, heavy){
  statusEl.textContent = heavy ? 'Applying… (re-running TTS, may take a moment)' : 'Applying…';
  try{
    const res=await fetch('/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task:S.taskPath,overrides})});
    const d=await res.json().catch(()=>({status:'error',message:'bad response'}));
    if(d.status!=='ok'){statusEl.textContent='Apply failed: '+(d.message||'');return;}
    S.rev++;
    if(typeof d.history_depth==='number') updateUndoBtn(d.history_depth);
    const cur=S.selected?.slide;
    await loadTask(S.taskPath);
    if(cur!=null)selectSlide(cur);
    statusEl.textContent='Applied ✓';
  }catch(e){statusEl.textContent='No server — run pipeline_server.py to edit';}
}

// ── Undo: server pops the most recent overrides.json snapshot ────────
function updateUndoBtn(depth){
  S.historyDepth = depth;
  if(!undoBtn) return;
  undoBtn.disabled = !depth;
  undoBtn.title = depth ? `Undo (${depth} edit${depth>1?'s':''} in history)` : 'Nothing to undo';
}
async function refreshHistoryDepth(){
  if(!S.taskPath) return;
  try{
    const r=await fetch('/history-depth',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task:S.taskPath})});
    const d=await r.json();
    if(d.status==='ok') updateUndoBtn(d.history_depth||0);
  }catch(_){ /* no server — leave button disabled */ }
}
undoBtn?.addEventListener('click', async()=>{
  if(!S.taskPath||undoBtn.disabled) return;
  undoBtn.disabled=true;
  statusEl.textContent='Undoing…';
  try{
    const r=await fetch('/undo',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task:S.taskPath})});
    const d=await r.json().catch(()=>({status:'error',message:'bad response'}));
    if(d.status!=='ok'){statusEl.textContent='Undo failed: '+(d.message||'');return;}
    if(typeof d.history_depth==='number') updateUndoBtn(d.history_depth);
    S.rev++;
    const cur=S.selected?.slide;
    await loadTask(S.taskPath);
    if(cur!=null)selectSlide(cur);
    statusEl.textContent='Undone ✓';
  }catch(e){statusEl.textContent='Undo failed: '+e.message;}
});

// ── Slides ──────────────────────────────────────────────────────────
function renderSlideList(){
  slideList.innerHTML='';
  for(const s of S.slides){
    const meta=S.meta.get(s.slide)||s;
    const btn=document.createElement('button');btn.className='slide-btn';
    btn.innerHTML=`<strong>Slide ${pad(s.slide)}</strong><span>${fmt(s.duration)}s · ${meta.layers.length} layers</span>`;
    btn.addEventListener('click',()=>selectSlide(s.slide));slideList.appendChild(btn);
  }
}

function selectSlide(n){
  const s=S.slides.find(x=>x.slide===n);if(!s)return;
  stopPlay();S.selected=s;S.selLayer=null;S.splitMode=null;
  if(layerEdit) layerEdit.hidden=true;
  document.querySelectorAll('.slide-btn').forEach(b=>b.classList.toggle('active',b.textContent.includes(`Slide ${pad(n)}`)));
  renderSlide();
}

function renderSlide(){
  const s=S.selected,meta=S.meta.get(s.slide)||s,tim=S.timing[sk(s.slide)]||{};
  const layers=meta.layers||[],skipped=layers.filter(l=>l.type==='key_point_card').length;
  slideLabel.textContent=sk(s.slide);slideTitleEl.textContent=`${layers.length} layers, ${skipped} skipped`;
  renderStats(s,layers);renderNarration(tim);renderPreview(s,layers);renderTimeline(s,meta,tim);
  const nk = `notes-${S.taskPath}-${sk(s.slide)}`;
  if(notesEl) notesEl.value = localStorage.getItem(nk)||'';
}

// ── Notes (supplementary free-text -> overrides.json) ────────────────
if(notesEl) notesEl.addEventListener('input',()=>{
  const nk = `notes-${S.taskPath}-${sk(S.selected.slide)}`;
  localStorage.setItem(nk, notesEl.value);
  fetch('/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task:S.taskPath,overrides:{[sk(S.selected.slide)]:{notes:notesEl.value}}})}).catch(()=>{});
});

// ── Edit handlers ───────────────────────────────────────────────────
saveNarration?.addEventListener('click',()=>{ if(S.selected) applyEdit({[sk(S.selected.slide)]:{narration:narrationEl.value}}, true); });

// Voice & rate live in the right-side picker; populating + rate slider wiring.
voiceLang?.addEventListener('change',()=>populateVoices(voiceLang.value));
function formatRate(n){ const v=parseInt(n,10); return (v>=0?`+${v}%`:`${v}%`); }
rateSlider?.addEventListener('input',()=>{ rateLabel.textContent=formatRate(rateSlider.value); });
voiceApply?.addEventListener('click',()=>{
  const v={voice:voiceSel.value, rate:formatRate(rateSlider.value)};
  S.voice=v.voice; S.rate=v.rate;
  applyEdit({voice:v}, true);
});

// Subtitle style (size + colors). Burned-vs-clean toggle is consumed at MP4
// export time; style is persisted to caption_config.json server-side.
function hexToAss(hex,alpha){ // ASS uses &HAABBGGRR (alpha inverted, 00=opaque)
  const m=/^#?([0-9a-f]{6})$/i.exec(hex||'');
  if(!m) return '&H00FFFFFF';
  const r=m[1].substr(0,2),g=m[1].substr(2,2),b=m[1].substr(4,2);
  const a=Math.max(0,Math.min(255,255-(alpha??255))).toString(16).padStart(2,'0').toUpperCase();
  return `&H${a}${b.toUpperCase()}${g.toUpperCase()}${r.toUpperCase()}`;
}
subSize?.addEventListener('input',()=>{ subSizeLabel.textContent=subSize.value; updateSubPreview(); });
subFg?.addEventListener('input',updateSubPreview);
subBg?.addEventListener('input',updateSubPreview);
function updateSubPreview(){
  if(!subPreview) return;
  subPreview.style.fontSize=`${Math.round(subSize.value*1.4)}px`;
  subPreview.style.color=subFg.value;
  // Approximate the ~40% backColour HF burns.
  const b=subBg.value;
  subPreview.style.background=b.length===7?`${b}66`:b;
}
subApply?.addEventListener('click',()=>{
  const style={
    size: parseInt(subSize.value,10),
    primary_colour: hexToAss(subFg.value, 255),
    back_colour:    hexToAss(subBg.value, 0x66),
  };
  applyEdit({caption_style: style}, false);
});

// Preview-mode pills: animation (default) vs segmentation debug (no-anim, bbox outlines).
modePills?.addEventListener('click',e=>{
  const b=e.target.closest('.mode-pill'); if(!b) return;
  S.previewMode=b.dataset.mode;
  modePills.querySelectorAll('.mode-pill').forEach(p=>p.classList.toggle('active',p===b));
  if(S.selected) renderSlide();
});

function renderStats(s,layers){
  const lowN = layers.filter(l=>l.ocr && l.ocr.line_count>0 && l.ocr.confidence<LOW_CONF).length;
  const medN = layers.filter(l=>(!l.ocr||l.ocr.line_count===0) && TEXT_TYPES.has(l.type)).length;
  const flagRows = (lowN?`<dt>Low-conf OCR</dt><dd style="color:var(--danger);font-weight:700">${lowN}</dd>`:'')
    + (medN?`<dt>Missing OCR (text type)</dt><dd style="color:var(--accent2);font-weight:700">${medN}</dd>`:'');
  statsEl.innerHTML=`<dt>Canvas</dt><dd>${s.width}&times;${s.height}</dd><dt>Duration</dt><dd>${fmt(s.duration)}s</dd><dt>Layers</dt><dd>${layers.length}</dd>${flagRows}`;
}

function renderNarration(tim){
  narrationEl.value=tim.script||'';
  if(tim.voiceover_file){audioEl.src=`${tRoot(S.taskPath)}${tim.voiceover_file}?v=${S.rev}`;audioEl.hidden=false;}else{audioEl.removeAttribute('src');audioEl.hidden=true;}
}

// Type → outline color for segmentation-debug mode.
const SEG_COLORS={title:'#22d3ee',table:'#a78bfa',text_block:'#facc15',illustration:'#34d399',
  icon:'#fb7185',arrow:'#f97316',annotation:'#ec4899',highlight:'#eab308',key_point_card:'#94a3b8'};
function segColor(t){return SEG_COLORS[t]||'#94d3eb';}

function renderPreview(s,layers){
  const rt=tRoot(S.taskPath),key=sk(s.slide);
  const map={original:`${rt}output/${key}/original.png`,background:`${rt}output/${key}/background.png`,debug:`${rt}work_preview/element_debug/${key}_debug.jpg`,gallery:`${rt}work_preview/${key}_layer_gallery.jpg`};
  if(S.view!=='composite'){preview.style.display='none';assetPreview.style.display='block';assetPreview.src=map[S.view];return;}
  assetPreview.style.display='none';preview.style.display='block';preview.innerHTML='';
  const bg=document.createElement('img');bg.className='bg';bg.src=`${rt}output/${key}/background.png`;preview.appendChild(bg);
  const sub=document.createElement('div');sub.className='sub-overlay';sub.id='subOverlay';preview.appendChild(sub);
  const segMode=S.previewMode==='debug';
  let idx=0;
  for(const l of layers){
    if(l.type==='key_point_card'&&!S.showSkipped) continue;
    idx++;
    const anim=l.animation||'fade-in',dur=l.duration||.7;
    const wrap=document.createElement('div');
    wrap.style.position='absolute';wrap.style.left=`${l.x/s.width*100}%`;wrap.style.top=`${l.y/s.height*100}%`;
    wrap.style.width=`${l.width/s.width*100}%`;wrap.style.height=`${l.height/s.height*100}%`;wrap.style.zIndex=l.z_index;
    if(l.hidden) wrap.style.opacity='0.25';
    const img=document.createElement('img');
    img.className=segMode?'layer seg-shown':`layer ${anim}${l.type==='key_point_card'?' skipped':''}`;
    img.style.width='100%';img.style.height='100%';
    img.dataset.start=l.start;img.dataset.layer=l.name;img.style.setProperty('--d',`${dur}s`);
    img.src=`${rt}output/${key}/${l.name}`;
    wrap.appendChild(img);
    if(segMode){
      const c=segColor(l.type);
      wrap.style.outline=`2px solid ${c}`;
      wrap.style.outlineOffset='-2px';
      wrap.style.boxShadow=`inset 0 0 0 9999px ${c}11`;  // faint tint
      const tag=document.createElement('div');
      tag.className='seg-tag';
      tag.style.cssText=`position:absolute;left:0;top:0;padding:2px 6px;background:${c};color:#08111c;font:600 11px/1.2 system-ui;border-radius:0 0 4px 0;pointer-events:none;z-index:5`;
      tag.textContent=`${idx} · ${l.type}`;
      wrap.appendChild(tag);
    } else {
      const num=document.createElement('div');num.className='layer-idx';num.textContent=idx;
      wrap.appendChild(num);
    }
    preview.appendChild(wrap);
  }
}

function renderTimeline(s,meta,tim){
  const layers=meta.layers||[],dur=Math.max(s.duration,.1);
  timelineEl.innerHTML='';
  for(const l of layers){
    const sp=Math.max(0,Math.min(100,l.start/dur*100)),wp=Math.max(.5,Math.min(100-sp,l.duration/dur*100));
    const row=document.createElement('div');row.className='timeline-row';
    row.innerHTML=`<span>${l.type}</span><div class="track"><span class="bar${l.type==='key_point_card'?' skipped':''}" style="left:${sp}%;width:${wp}%"></span><span class="cue-dot" style="left:${sp}%"></span></div><span>${fmt(l.start)}s</span>`;
    timelineEl.appendChild(row);
  }
}

function renderLayers(s,meta){
  layerList.innerHTML='';
  renderSegActionBar();
  let idx=0;
  for(const l of meta.layers||[]){
    idx++;
    const item=document.createElement('div');item.className='layer-item';item.dataset.layer=l.name;
    if(S.multiSel.has(l.name)) item.classList.add('multi-active');
    if(l.hidden) item.classList.add('layer-hidden');
    const low = l.ocr && l.ocr.line_count>0 && l.ocr.confidence<LOW_CONF;
    const med = !low && (!l.ocr || l.ocr.line_count===0) && TEXT_TYPES.has(l.type);
    if(low) item.classList.add('low-conf');
    else if(med) item.classList.add('med-conf');
    const eye=l.hidden?'🙈':'👁';
    const groupTag=l.merge_group
      ? `<span title="group ${l.merge_group}" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${groupColor(l.merge_group)};margin-right:4px;vertical-align:middle"></span>` : '';
    let ocrTag = '';
    if(l.ocr && l.ocr.line_count>0){
      ocrTag = `<div class="layer-ocr${low?' low':''}" data-layer-ocr="1" title="OCR conf ${l.ocr.confidence} · ${l.ocr.line_count} line(s) — click to expand/edit">`+
        `${low?'⚠ ':''}${l.ocr.corrected?'<span class="layer-ocr-tag" title="manually corrected">✎</span> ':''}<span class="layer-ocr-conf">${(l.ocr.confidence*100).toFixed(0)}%</span> ${esc(l.ocr.text)}`+
        `</div>`;
    } else if(med){
      ocrTag = `<div class="layer-ocr med" data-layer-ocr="1" title="${l.type} usually contains text — click to add OCR text manually">`+
        `◑ <span class="layer-ocr-conf">—</span> <em style="opacity:.75">no OCR text · click to add</em>`+
        `</div>`;
    }
    item.innerHTML=
      `<div class="layer-row-top">`+
        `<span class="layer-num">${idx}</span>`+
        `<div class="layer-info"><div class="layer-name">${groupTag}${esc(l.name)}</div>`+
          `<div class="layer-meta"><span>${fmt(l.start)}s</span><span>${fmt(l.duration)}s</span><span>${l.animation}</span><span>z${l.z_index}</span></div>`+
          ocrTag+
        `</div>`+
        `<span class="pill">${l.type}</span>`+
      `</div>`+
      `<div class="layer-acts">`+
        `<button class="la-btn" data-act="hide" title="${l.hidden?'Show this layer':'Hide this layer'}">${eye}</button>`+
        `<button class="la-btn" data-act="zup" title="Bring forward (z+1)">↑</button>`+
        `<button class="la-btn" data-act="zdn" title="Send back (z-1)">↓</button>`+
        `<button class="la-btn" data-act="split" title="Split this layer into two">✂</button>`+
        `<span style="flex:1"></span>`+
        `<span style="font-size:11px;color:var(--muted)">${l.width}&times;${l.height} @ ${l.x},${l.y}</span>`+
      `</div>`;
    item.addEventListener('click',e=>{
      if(e.target.closest('.la-btn')) return;
      if(e.ctrlKey||e.metaKey){
        if(S.multiSel.has(l.name)) S.multiSel.delete(l.name); else S.multiSel.add(l.name);
        renderLayers(s,meta);
        return;
      }
      S.multiSel.clear();selectLayer(l.name,idx);renderSegActionBar();
    });
    item.querySelectorAll('.la-btn').forEach(b=>{
      b.addEventListener('click',e=>{e.stopPropagation();handleLayerAct(s,l,b.dataset.act);});
    });
    layerList.appendChild(item);
  }
  attachLayerOcrHandlers();
}

function renderSegActionBar(){
  let bar=document.getElementById('segActions');
  if(!bar){
    bar=document.createElement('div');bar.id='segActions';
    bar.style.cssText='display:none;margin:6px 0;padding:6px 8px;background:rgba(99,102,241,.15);border:1px solid rgba(99,102,241,.4);border-radius:6px;font-size:11px';
    layerList.parentElement.insertBefore(bar,layerList);
  }
  const n=S.multiSel.size;
  if(n===0){bar.style.display='none';return;}
  bar.style.display='block';
  bar.innerHTML=
    `<span>${n} selected · </span>`+
    (n>=2?`<button class="hf-btn" id="segMerge">Merge</button> `:'')+
    `<button class="hf-btn" id="segUngroup">Ungroup</button> `+
    `<button class="hf-btn" id="segHide">Hide</button> `+
    `<button class="hf-btn" id="segShow">Show</button> `+
    `<button class="hf-btn" id="segClear">Clear</button>`;
  const wireGroup=()=>{
    if(!S.selected) return;
    const names=Array.from(S.multiSel);
    // pick a fresh group key: g1, g2, ...
    const used=new Set((S.meta.get(S.selected.slide)?.layers||[]).map(x=>x.merge_group).filter(Boolean));
    let i=1;while(used.has(`g${i}`)) i++;
    const g=`g${i}`;
    const layers={};names.forEach(n=>{layers[n]={merge_group:g};});
    applyEdit({[sk(S.selected.slide)]:{layers}},false);
    S.multiSel.clear();
  };
  document.getElementById('segMerge')?.addEventListener('click',wireGroup);
  document.getElementById('segUngroup').addEventListener('click',()=>{
    const layers={};Array.from(S.multiSel).forEach(n=>{layers[n]={merge_group:null};});
    applyEdit({[sk(S.selected.slide)]:{layers}},false);S.multiSel.clear();
  });
  document.getElementById('segHide').addEventListener('click',()=>{
    const layers={};Array.from(S.multiSel).forEach(n=>{layers[n]={hidden:true};});
    applyEdit({[sk(S.selected.slide)]:{layers}},false);S.multiSel.clear();
  });
  document.getElementById('segShow').addEventListener('click',()=>{
    const layers={};Array.from(S.multiSel).forEach(n=>{layers[n]={hidden:false};});
    applyEdit({[sk(S.selected.slide)]:{layers}},false);S.multiSel.clear();
  });
  document.getElementById('segClear').addEventListener('click',()=>{
    S.multiSel.clear();renderLayers(S.selected,S.meta.get(S.selected.slide)||S.selected);
  });
}

function handleLayerAct(s,l,act){
  console.log('[layer-act]', act, 'on', l.name, 'currently hidden=', l.hidden);
  const slideKey=sk(s.slide);
  statusEl.textContent = `${act} on ${l.name}…`;
  if(act==='hide') applyEdit({[slideKey]:{layers:{[l.name]:{hidden:!l.hidden}}}},false);
  else if(act==='zup') applyEdit({[slideKey]:{layers:{[l.name]:{z:(l.z_index||0)+1}}}},false);
  else if(act==='zdn') applyEdit({[slideKey]:{layers:{[l.name]:{z:Math.max(0,(l.z_index||0)-1)}}}},false);
  else if(act==='split') startSplit(s, l);
}

function selectLayer(name,num){
  // Switching layers cancels in-progress split.
  if(S.splitMode && S.splitMode.name!==name) S.splitMode=null;
  S.selLayer=name;
  const s=S.selected,l=(S.meta.get(s.slide)||s).layers.find(x=>x.name===name);
  if(l){leName.textContent=name;leStart.value=l.start;leDur.value=l.duration;leAnim.value=l.animation;layerEdit.hidden=false;}
  // Re-render preview so the bbox handles attach to the newly-selected layer.
  if(S.selected) renderPreview(S.selected,(S.meta.get(S.selected.slide)||S.selected).layers||[]);
  document.querySelectorAll('.layer-item').forEach(e=>e.classList.toggle('active',e.dataset.layer===name));
  document.querySelectorAll('.stage-preview .layer').forEach(e=>e.classList.toggle('highlight',e.dataset.layer===name));
  document.querySelectorAll('.stage-preview .layer-idx').forEach(e=>{e.style.display='none'});
  if(name){const el=document.querySelector(`.stage-preview .layer.highlight`);if(el){const p=el.parentElement;if(p){const li=p.querySelector('.layer-idx');if(li)li.style.display='flex';}}}
}

// ── Bbox drag (v2 segmentation edit) ────────────────────────────────
function attachBboxHandles(wrap, layer, slide){
  // Lift the selected wrap above every other layer so its handles aren't
  // hidden by a layer with higher baseline z-index.
  wrap.style.zIndex=10000;
  wrap.style.outline='2px solid #facc15';
  wrap.style.outlineOffset='-2px';
  const handles=[['nw',0,0,'nwse-resize'],['ne',1,0,'nesw-resize'],
                 ['sw',0,1,'nesw-resize'],['se',1,1,'nwse-resize']];
  for(const [name,fx,fy,cursor] of handles){
    const h=document.createElement('div');
    h.className='bbox-handle';h.dataset.corner=name;
    h.style.cssText=`position:absolute;left:${fx*100}%;top:${fy*100}%;width:16px;height:16px;`+
      `transform:translate(-50%,-50%);background:#facc15;border:2px solid #08111c;`+
      `border-radius:50%;cursor:${cursor};z-index:10001;box-shadow:0 0 6px rgba(0,0,0,.6);`;
    h.addEventListener('mousedown',e=>startBboxDrag(e, wrap, layer, slide, name));
    h.addEventListener('click',e=>e.stopPropagation());
    wrap.appendChild(h);
  }
  const move=document.createElement('div');
  move.className='bbox-move';
  move.style.cssText='position:absolute;inset:14px;cursor:move;z-index:9998;background:transparent';
  move.addEventListener('mousedown',e=>startBboxDrag(e, wrap, layer, slide, 'move'));
  move.addEventListener('click',e=>e.stopPropagation());
  wrap.appendChild(move);
}

function startBboxDrag(e, wrap, layer, slide, corner){
  e.preventDefault();e.stopPropagation();
  const previewRect=preview.getBoundingClientRect();
  const sx=slide.width/previewRect.width, sy=slide.height/previewRect.height;
  const startMX=e.clientX, startMY=e.clientY;
  const start={x:layer.x, y:layer.y, w:layer.width, h:layer.height};
  function onMove(ev){
    const dx=(ev.clientX-startMX)*sx, dy=(ev.clientY-startMY)*sy;
    let {x,y,w,h}=start;
    if(corner==='move'){x+=dx;y+=dy;}
    else {
      if(corner.includes('w')){x+=dx;w-=dx;}
      if(corner.includes('e')){w+=dx;}
      if(corner.includes('n')){y+=dy;h-=dy;}
      if(corner.includes('s')){h+=dy;}
    }
    // Clamp to slide + minimum size.
    if(w<20){w=20;}if(h<20){h=20;}
    x=Math.max(0,Math.min(slide.width-w, Math.round(x)));
    y=Math.max(0,Math.min(slide.height-h, Math.round(y)));
    w=Math.round(Math.min(w, slide.width-x));
    h=Math.round(Math.min(h, slide.height-y));
    wrap.style.left=`${x/slide.width*100}%`;
    wrap.style.top=`${y/slide.height*100}%`;
    wrap.style.width=`${w/slide.width*100}%`;
    wrap.style.height=`${h/slide.height*100}%`;
    wrap.dataset.bbox=`${x},${y},${w},${h}`;
  }
  function onUp(){
    document.removeEventListener('mousemove',onMove);
    document.removeEventListener('mouseup',onUp);
    if(!wrap.dataset.bbox) return;
    const [x,y,w,h]=wrap.dataset.bbox.split(',').map(Number);
    if(x===start.x && y===start.y && w===start.w && h===start.h) return;  // no change
    statusEl.textContent='Re-extracting layer with new bbox…';
    applyEdit({[sk(slide.slide)]:{layers:{[layer.name]:{bbox:[x,y,w,h]}}}}, false);
  }
  document.addEventListener('mousemove',onMove);
  document.addEventListener('mouseup',onUp);
}

// ── OCR review (expand snippet, edit, save as overrides.ocr_corrected) ─
function attachLayerOcrHandlers(){
  layerList.querySelectorAll('.layer-ocr[data-layer-ocr="1"]').forEach(el=>{
    if(el.dataset.bound) return;
    el.dataset.bound='1';
    el.addEventListener('click',e=>{
      e.stopPropagation();
      const row=el.closest('.layer-item');
      const name=row?.dataset.layer;
      if(name) openOcrModal(name);
    });
  });
}
function openOcrModal(layerName){
  if(!S.selected) return;
  const layer=(S.meta.get(S.selected.slide)||S.selected).layers.find(l=>l.name===layerName);
  if(!layer) return;
  const dlg=ensureOcrDialog();
  const o=layer.ocr||{text:'',confidence:0,line_count:0};
  document.getElementById('ocrModalTitle').textContent=`OCR · ${layerName}`;
  document.getElementById('ocrModalMeta').innerHTML=
    `<span>type <b>${layer.type}</b></span>`+
    `<span>conf <b>${o.line_count?(o.confidence*100).toFixed(1)+'%':'—'}</b></span>`+
    `<span>lines <b>${o.line_count||0}</b></span>`+
    (o.corrected?`<span style="color:var(--ok)"><b>✎ manually corrected</b></span>`:'')+
    `<span>bbox <b>${layer.x},${layer.y},${layer.width}&times;${layer.height}</b></span>`;
  const ta=document.getElementById('ocrModalText');
  ta.value=o.text||'';
  ta.dataset.layer=layerName;
  ta.dataset.original=o.text||'';
  ta.dataset.wasCorrected=o.corrected?'1':'';
  dlg.classList.add('show');
  setTimeout(()=>ta.focus(),50);
}

function ensureOcrDialog(){
  let dlg=document.getElementById('ocrDialog');
  if(dlg) return dlg;
  dlg=document.createElement('div');
  dlg.id='ocrDialog';dlg.className='dialog';
  dlg.innerHTML=
    `<div class="dialog-card" style="min-width:480px;max-width:640px">`+
      `<h3 id="ocrModalTitle">OCR</h3>`+
      `<div class="ocr-modal-body">`+
        `<div id="ocrModalMeta" class="ocr-modal-meta"></div>`+
        `<textarea id="ocrModalText" placeholder="No OCR text yet — type manually if this region has text the model missed."></textarea>`+
        `<div class="ocr-modal-actions">`+
          `<button id="ocrModalSave" class="play-btn">Save correction</button>`+
          `<button id="ocrModalReset" class="reset-btn">Reset to auto-OCR</button>`+
          `<span style="flex:1"></span>`+
          `<button id="ocrModalCancel" class="hf-btn">Close</button>`+
        `</div>`+
      `</div>`+
    `</div>`;
  document.querySelector('main.app').appendChild(dlg);
  document.getElementById('ocrModalCancel').addEventListener('click',()=>dlg.classList.remove('show'));
  document.getElementById('ocrModalReset').addEventListener('click',ocrModalReset);
  document.getElementById('ocrModalSave').addEventListener('click',ocrModalSave);
  dlg.addEventListener('click',e=>{ if(e.target===dlg) dlg.classList.remove('show'); });
  return dlg;
}

function ocrModalSave(){
  const ta=document.getElementById('ocrModalText');
  if(!ta||!S.selected) return;
  const layerName=ta.dataset.layer;
  const text=ta.value.trim();
  const original=ta.dataset.original;
  // Empty text + nothing was corrected before → nothing to save.
  if(!text && !ta.dataset.wasCorrected){
    document.getElementById('ocrDialog').classList.remove('show');
    return;
  }
  // Storing the auto-OCR text verbatim is the same as no correction; drop it.
  const value = (text && text !== original) ? text : null;
  applyEdit({[sk(S.selected.slide)]:{layers:{[layerName]:{ocr_corrected:value}}}}, false);
  document.getElementById('ocrDialog').classList.remove('show');
}
function ocrModalReset(){
  const ta=document.getElementById('ocrModalText');
  if(!ta||!S.selected) return;
  const layerName=ta.dataset.layer;
  applyEdit({[sk(S.selected.slide)]:{layers:{[layerName]:{ocr_corrected:null}}}}, false);
  document.getElementById('ocrDialog').classList.remove('show');
}

// ── Split (v3 segmentation edit) ────────────────────────────────────
function startSplit(s, layer){
  S.selLayer=layer.name;
  // Default to cut mode; toolbar lets user switch to bboxes mode (two
  // independent, possibly non-adjacent regions). parentBbox is captured so
  // bboxes-mode defaults to two halves of the layer.
  S.splitMode={
    name:layer.name, kind:'cut', axis:'x', at:0.5,
    parentBbox:[layer.x, layer.y, layer.width, layer.height],
    bboxes:[
      [layer.x, layer.y, Math.floor(layer.width/2), layer.height],
      [layer.x + Math.floor(layer.width/2), layer.y, layer.width - Math.floor(layer.width/2), layer.height],
    ],
  };
  statusEl.textContent='Split mode — drag the yellow line, then Confirm.';
  renderPreview(S.selected,(S.meta.get(S.selected.slide)||S.selected).layers||[]);
  renderSplitToolbar();
  document.querySelectorAll('.layer-item').forEach(e=>e.classList.toggle('active',e.dataset.layer===layer.name));
}

function attachSplitOverlay(wrap, layer, slide){
  wrap.style.zIndex=10000;
  wrap.style.outline='2px solid #facc15';
  wrap.style.outlineOffset='-2px';
  const m=S.splitMode;
  if(m.kind==='bboxes'){
    // Bboxes mode renders OUTSIDE the parent wrap (rectangles may be non-
    // adjacent and extend anywhere on the slide), so we draw on the
    // stage-preview directly.
    attachSplitBboxOverlays(slide);
    return;
  }
  const aShade=document.createElement('div'),bShade=document.createElement('div');
  const shadeBase='position:absolute;pointer-events:none;z-index:9999';
  aShade.style.cssText=shadeBase+';background:rgba(20,184,166,.20)';
  bShade.style.cssText=shadeBase+';background:rgba(236,72,153,.20)';
  if(m.axis==='x'){
    const p=m.at*100;
    aShade.style.left='0';aShade.style.top='0';aShade.style.width=`${p}%`;aShade.style.height='100%';
    bShade.style.left=`${p}%`;bShade.style.top='0';bShade.style.width=`${100-p}%`;bShade.style.height='100%';
  }else{
    const p=m.at*100;
    aShade.style.left='0';aShade.style.top='0';aShade.style.width='100%';aShade.style.height=`${p}%`;
    bShade.style.left='0';bShade.style.top=`${p}%`;bShade.style.width='100%';bShade.style.height=`${100-p}%`;
  }
  wrap.appendChild(aShade);wrap.appendChild(bShade);

  const handle=document.createElement('div');
  const baseHandle='position:absolute;background:#facc15;z-index:10001;box-shadow:0 0 8px rgba(0,0,0,.6)';
  if(m.axis==='x'){
    handle.style.cssText=baseHandle+`;left:${m.at*100}%;top:0;width:6px;height:100%;cursor:ew-resize;transform:translateX(-3px)`;
  }else{
    handle.style.cssText=baseHandle+`;left:0;top:${m.at*100}%;width:100%;height:6px;cursor:ns-resize;transform:translateY(-3px)`;
  }
  handle.addEventListener('mousedown',e=>startSplitDrag(e, wrap));
  handle.addEventListener('click',e=>e.stopPropagation());
  wrap.appendChild(handle);
}

// Bboxes mode: two independently draggable rectangles drawn on the stage-preview.
// Each rect mirrors S.splitMode.bboxes[i]; drag-move + 4 corner resize handles
// rewrite the entry, then renderPreview/toolbar refresh on commit.
function attachSplitBboxOverlays(slide){
  const colors=['rgba(20,184,166,.85)','rgba(236,72,153,.85)'];
  S.splitMode.bboxes.forEach((bb,i)=>{
    const rect=document.createElement('div');
    rect.style.cssText='position:absolute;outline:2px dashed '+colors[i]+';outline-offset:-1px;'+
      'background:'+colors[i].replace('.85','.18')+';z-index:'+(10005+i);
    rect.style.left=`${bb[0]/slide.width*100}%`;
    rect.style.top=`${bb[1]/slide.height*100}%`;
    rect.style.width=`${bb[2]/slide.width*100}%`;
    rect.style.height=`${bb[3]/slide.height*100}%`;
    const label=document.createElement('div');
    label.textContent = i===0?'A':'B';
    label.style.cssText='position:absolute;top:-12px;left:-2px;padding:1px 6px;border-radius:8px;'+
      'background:'+colors[i]+';color:#08111c;font-weight:800;font-size:10px;z-index:'+(10007+i);
    rect.appendChild(label);
    // Corner handles + center move zone — reuse the bbox-drag interaction.
    const handles=[['nw',0,0,'nwse-resize'],['ne',1,0,'nesw-resize'],
                   ['sw',0,1,'nesw-resize'],['se',1,1,'nwse-resize']];
    for(const [corner,fx,fy,cursor] of handles){
      const h=document.createElement('div');
      h.style.cssText=`position:absolute;left:${fx*100}%;top:${fy*100}%;width:14px;height:14px;`+
        `transform:translate(-50%,-50%);background:${colors[i]};border:2px solid #08111c;`+
        `border-radius:50%;cursor:${cursor};z-index:${10010+i}`;
      h.addEventListener('mousedown',e=>startSplitBboxDrag(e, slide, i, corner));
      h.addEventListener('click',e=>e.stopPropagation());
      rect.appendChild(h);
    }
    const mover=document.createElement('div');
    mover.style.cssText='position:absolute;inset:12px;cursor:move;z-index:'+(10006+i);
    mover.addEventListener('mousedown',e=>startSplitBboxDrag(e, slide, i, 'move'));
    mover.addEventListener('click',e=>e.stopPropagation());
    rect.appendChild(mover);
    preview.appendChild(rect);
  });
}

function startSplitBboxDrag(e, slide, idx, corner){
  e.preventDefault(); e.stopPropagation();
  const previewRect=preview.getBoundingClientRect();
  const sx=slide.width/previewRect.width, sy=slide.height/previewRect.height;
  const startMX=e.clientX, startMY=e.clientY;
  const start=[...S.splitMode.bboxes[idx]];
  function onMove(ev){
    const dx=(ev.clientX-startMX)*sx, dy=(ev.clientY-startMY)*sy;
    let [x,y,w,h]=start;
    if(corner==='move'){ x+=dx; y+=dy; }
    else{
      if(corner.includes('w')){ x+=dx; w-=dx; }
      if(corner.includes('e')){ w+=dx; }
      if(corner.includes('n')){ y+=dy; h-=dy; }
      if(corner.includes('s')){ h+=dy; }
    }
    if(w<20) w=20; if(h<20) h=20;
    x=Math.max(0, Math.min(slide.width-w, Math.round(x)));
    y=Math.max(0, Math.min(slide.height-h, Math.round(y)));
    w=Math.round(Math.min(w, slide.width-x));
    h=Math.round(Math.min(h, slide.height-y));
    S.splitMode.bboxes[idx]=[x,y,w,h];
    renderPreview(S.selected,(S.meta.get(S.selected.slide)||S.selected).layers||[]);
    renderSplitToolbar();
  }
  function onUp(){
    document.removeEventListener('mousemove',onMove);
    document.removeEventListener('mouseup',onUp);
  }
  document.addEventListener('mousemove',onMove);
  document.addEventListener('mouseup',onUp);
}

function startSplitDrag(e, wrap){
  e.preventDefault();e.stopPropagation();
  const wrapRect=wrap.getBoundingClientRect();
  function onMove(ev){
    const m=S.splitMode;if(!m)return;
    if(m.axis==='x'){
      m.at=Math.max(0.05,Math.min(0.95,(ev.clientX-wrapRect.left)/wrapRect.width));
    }else{
      m.at=Math.max(0.05,Math.min(0.95,(ev.clientY-wrapRect.top)/wrapRect.height));
    }
    renderPreview(S.selected,(S.meta.get(S.selected.slide)||S.selected).layers||[]);
    renderSplitToolbar();
  }
  function onUp(){
    document.removeEventListener('mousemove',onMove);
    document.removeEventListener('mouseup',onUp);
  }
  document.addEventListener('mousemove',onMove);
  document.addEventListener('mouseup',onUp);
}

function renderSplitToolbar(){
  let bar=document.getElementById('splitActions');
  if(!bar){
    bar=document.createElement('div');bar.id='splitActions';
    bar.style.cssText='display:none;margin:6px 0;padding:8px;background:rgba(250,204,21,.15);border:1px solid rgba(250,204,21,.5);border-radius:6px;font-size:12px';
    layerList.parentElement.insertBefore(bar,layerList);
  }
  const m=S.splitMode;
  if(!m){bar.style.display='none';return;}
  bar.style.display='block';
  const cutMode = m.kind==='cut';
  const axisRow = cutMode
    ? `<button class="hf-btn" id="splitAxisX" title="Vertical cut (left/right)">${m.axis==='x'?'● Vertical':'Vertical'}</button>`+
      `<button class="hf-btn" id="splitAxisY" title="Horizontal cut (top/bottom)">${m.axis==='y'?'● Horizontal':'Horizontal'}</button>`+
      `<span style="font-size:11px;color:var(--muted)">at ${(m.at*100).toFixed(0)}%</span>`
    : `<span style="font-size:11px;color:var(--muted)">drag rectangles A &amp; B; they can be non-adjacent or overlap</span>`;
  bar.innerHTML=
    `<div style="margin-bottom:6px"><b>Split mode</b> · <span style="opacity:.8">${m.name}</span></div>`+
    `<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-bottom:6px">`+
      `<button class="hf-btn" id="splitKindCut" title="Single cut into two adjacent halves">${cutMode?'● Cut':'Cut'}</button>`+
      `<button class="hf-btn" id="splitKindBboxes" title="Two independent regions (may be non-adjacent)">${!cutMode?'● Bboxes':'Bboxes'}</button>`+
    `</div>`+
    `<div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">`+
      axisRow+
      `<span style="flex:1"></span>`+
      `<button class="hf-btn" id="splitConfirm">Confirm</button>`+
      `<button class="hf-btn" id="splitCancel">Cancel</button>`+
    `</div>`;
  document.getElementById('splitKindCut').onclick=()=>{S.splitMode.kind='cut';splitRefresh();};
  document.getElementById('splitKindBboxes').onclick=()=>{S.splitMode.kind='bboxes';splitRefresh();};
  if(cutMode){
    document.getElementById('splitAxisX').onclick=()=>{S.splitMode.axis='x';splitRefresh();};
    document.getElementById('splitAxisY').onclick=()=>{S.splitMode.axis='y';splitRefresh();};
  }
  document.getElementById('splitConfirm').onclick=splitConfirm;
  document.getElementById('splitCancel').onclick=splitCancel;
}

function splitRefresh(){
  if(S.selected) renderPreview(S.selected,(S.meta.get(S.selected.slide)||S.selected).layers||[]);
  renderSplitToolbar();
}
function splitCancel(){S.splitMode=null;splitRefresh();statusEl.textContent='Split cancelled.';}
function splitConfirm(){
  const m=S.splitMode;if(!m||!S.selected)return;
  const slideKey=sk(S.selected.slide);
  const spec = m.kind==='bboxes'
    ? {bboxes: m.bboxes.map(b => b.map(v => Math.round(v)))}
    : {axis: m.axis, at: Math.round(m.at*100)/100};
  const payload={[slideKey]:{layers:{[m.name]:{split: spec}}}};
  S.splitMode=null;splitRefresh();
  statusEl.textContent='Splitting and re-extracting…';
  applyEdit(payload,false);
}

// ── Play ────────────────────────────────────────────────────────────
function stopPlay(){S.timers.forEach(clearTimeout);S.timers=[];preview.classList.remove('playing');
  document.querySelectorAll('.stage-preview .layer').forEach(e=>e.classList.remove('show'));
  const s=document.getElementById('subOverlay');if(s)s.classList.remove('show');
  try{audioEl.pause();audioEl.currentTime=0;}catch(_){}
  playBtn.textContent='▶ Play slide';playBtn.classList.remove('playing');S.playing=false;}

playBtn.addEventListener('click',()=>{
  if(S.playing){stopPlay();return;}
  const s=S.selected;if(!s)return;
  stopPlay();S.playing=true;preview.classList.add('playing');playBtn.textContent='■ Stop';playBtn.classList.add('playing');
  const layers=Array.from(document.querySelectorAll('.stage-preview .layer'));
  layers.forEach(el=>{const t=setTimeout(()=>el.classList.add('show'),parseFloat(el.dataset.start)*1000);S.timers.push(t);});
  const cues=S.subMap[sk(s.slide)]||[],sub=document.getElementById('subOverlay');
  cues.forEach(c=>{S.timers.push(setTimeout(()=>{if(sub){sub.textContent=c.text;sub.classList.add('show')}},c.start*1000));S.timers.push(setTimeout(()=>{if(sub)sub.classList.remove('show')},c.end*1000));});
  const tim=S.timing[sk(s.slide)];
  if(tim?.voiceover_file){audioEl.currentTime=0;audioEl.play().catch(()=>{});}
  S.timers.push(setTimeout(()=>stopPlay(),s.duration*1000+500));
});

// ── Tabs ────────────────────────────────────────────────────────────
document.querySelectorAll('.asset-tab').forEach(b=>{b.addEventListener('click',()=>{S.view=b.dataset.view;document.querySelectorAll('.asset-tab').forEach(t=>t.classList.toggle('active',t===b));if(S.selected)renderPreview(S.selected,(S.meta.get(S.selected.slide)||S.selected).layers||[]);});});
showSkipped?.addEventListener('change',e=>{S.showSkipped=e.target.checked;if(S.selected)renderPreview(S.selected,(S.meta.get(S.selected.slide)||S.selected).layers||[]);});

// ── Render dialog ───────────────────────────────────────────────────
renderBtn.addEventListener('click',()=>{renderDialog.classList.add('show');});
renderClose.addEventListener('click',()=>{renderDialog.classList.remove('show');});
renderGo.addEventListener('click',async()=>{
  renderGo.disabled=true;renderLog.textContent='Starting render…';
  try{
    const r=await fetch('/render',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task:S.taskPath,from:+renderFrom.value,to:+renderTo.value})});
    const d=await r.json();
    if(d.status==='ok') renderLog.textContent=d.output||'Done.';
    else renderLog.textContent='Error: '+(d.message||'');
  }catch(e){renderLog.textContent='Failed: '+e.message;}
  renderGo.disabled=false;
});

// ── Upload deck (.pptx auto-converts to PDF) ────────────────────────
let upAnimTimer=null,upStartTs=0;
function startUploadAnim(file){
  upStartTs=Date.now();
  const isPptx=/\.pptx?$/i.test(file.name);
  const phase=()=>{
    const sec=Math.floor((Date.now()-upStartTs)/1000);
    const dots='.'.repeat(1+sec%3);
    const label=isPptx
      ? (sec<5?`Uploading${dots}`:`Converting PPTX → PDF via LibreOffice (this is normal, 10–60s for big decks)${dots}`)
      : `Uploading${dots}`;
    upLog.textContent=`${label}\nElapsed: ${sec}s`;
  };
  phase();upAnimTimer=setInterval(phase,500);
}
function stopUploadAnim(){if(upAnimTimer){clearInterval(upAnimTimer);upAnimTimer=null;}}

uploadBtn.addEventListener('click',()=>{upLog.textContent='';uploadDialog.classList.add('show');});
upClose.addEventListener('click',()=>{uploadDialog.classList.remove('show');});
upGo.addEventListener('click',async()=>{
  const name=upTaskName.value.trim(),file=upFile.files[0];
  if(!name||!file){upLog.textContent='Need a task name and a .pptx/.ppt/.pdf file.';return;}
  upGo.disabled=true;startUploadAnim(file);
  try{
    const fd=new FormData();fd.append('task',name);fd.append('file',file);
    const r=await fetch('/ingest',{method:'POST',body:fd});
    const d=await r.json().catch(()=>({status:'error',message:'bad response'}));
    stopUploadAnim();
    upLog.textContent=(d.logs||[d.message||'(no message)']).join('\n');
    if(d.status==='ok'){
      await refreshTaskIndex();
      if(d.task){taskSelect.value=d.task;
        try{ await loadTask(d.task); }
        catch(_){ showPendingTask(d.task,d.pdf); }
        if(!S.slides.length) showPendingTask(d.task,d.pdf);
      }
    }
  }catch(e){stopUploadAnim();upLog.textContent='Failed: '+e.message;}
  upGo.disabled=false;
});

// ── Task / Theme ────────────────────────────────────────────────────
taskSelect.addEventListener('change',async e=>{taskSelect.disabled=true;await loadTask(e.target.value);taskSelect.disabled=false;});
deleteTaskBtn.addEventListener('click',async()=>{
  const tp=taskSelect.value;if(!tp)return;
  const label=S.tasks.find(t=>t.path===tp)?.label||tp;
  if(!confirm(`確定要刪除 task「${label}」嗎？\n資料夾 ${tp}/ 會被永久刪除，無法復原。`))return;
  deleteTaskBtn.disabled=true;
  try{
    const r=await fetch('/delete-task',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task:tp})});
    const d=await r.json().catch(()=>({status:'error',message:'bad response'}));
    if(d.status!=='ok'){alert('刪除失敗：'+(d.message||''));deleteTaskBtn.disabled=false;return;}
    await refreshTaskIndex();
    const next=S.tasks[0]?.path;
    if(next){taskSelect.value=next;await loadTask(next);}
    else{taskTitle.textContent='Pipeline';}
  }catch(e){alert('刪除失敗：'+e.message);}
  deleteTaskBtn.disabled=false;
});
const saved=localStorage.getItem('pui-theme')||'dark';document.documentElement.className=`theme-${saved}`;themeSelect.value=saved;
themeSelect.addEventListener('change',()=>{const t=themeSelect.value;document.documentElement.className=`theme-${t}`;localStorage.setItem('pui-theme',t);});
hfBtn?.addEventListener('click',()=>{window.open(`${tRoot(S.taskPath)}hyperframes/index.html`,'_blank');});

init();
