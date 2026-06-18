const pad=n=>String(n).padStart(2,'0'),fmt=v=>Number(v??0).toFixed(2),sk=n=>`slide_${pad(n)}`;

const taskSelect=document.getElementById('taskSelect'),themeSelect=document.getElementById('themeSelect'),
  taskTitle=document.getElementById('taskTitle'),statusEl=document.getElementById('status'),
  slideList=document.getElementById('slideList'),slideLabel=document.getElementById('slideLabel'),
  slideTitleEl=document.getElementById('slideTitle'),showSkipped=document.getElementById('showSkipped'),
  preview=document.getElementById('preview'),assetPreview=document.getElementById('assetPreview'),
  playBtn=document.getElementById('playBtn'),hfBtn=document.getElementById('hfBtn'),renderBtn=document.getElementById('renderBtn'),
  timelineEl=document.getElementById('timeline'),statsEl=document.getElementById('stats'),
  narrationEl=document.getElementById('narration'),audioEl=document.getElementById('audio'),
  layerList=document.getElementById('layerList'),notesEl=document.getElementById('notes'),
  renderDialog=document.getElementById('renderDialog'),renderFrom=document.getElementById('renderFrom'),
  renderTo=document.getElementById('renderTo'),renderGo=document.getElementById('renderGo'),
  renderClose=document.getElementById('renderClose'),renderLog=document.getElementById('renderLog');

const S={tasks:[],taskPath:null,canvas:{width:1920,height:1080},timing:null,slides:[],meta:new Map,
  subMap:{},selected:null,view:'composite',showSkipped:false,playing:false,timers:[],selLayer:null};
const root=new URL('../',window.location.href).href;
function tRoot(p){return new URL(`${p}/`,root).href;}
async function fj(p){const r=await fetch(p);if(!r.ok)throw Error(`${r.status}`);return r.json();}

// ── Load (driven by composition.json -- the resolved contract) ───────
async function loadTask(tp){
  const r=tRoot(tp);
  const comp=await fj(`${r}composition.json`);
  S.taskPath=tp;S.canvas=comp.canvas;
  // Normalize composition into the shape the render functions expect.
  S.slides=comp.slides.map(s=>({
    slide:s.index,width:comp.canvas.width,height:comp.canvas.height,duration:s.duration,
    layers:(s.layers||[]).map(l=>({
      name:l.image.split('/').pop(),type:l.type,
      x:l.bbox[0],y:l.bbox[1],width:l.bbox[2],height:l.bbox[3],
      z_index:l.z,animation:l.enter.type,start:l.start,duration:l.duration,
    })),
  }));
  S.meta=new Map(S.slides.map(s=>[s.slide,s]));
  S.timing={};
  for(const s of comp.slides) S.timing[sk(s.index)]={script:s.narration,voiceover_file:s.audio,start:s.start,end:s.end};
  // Subtitles for synced captions during Play (still produced by build_timeline).
  S.subMap={};
  try{
    const srt=await(await fetch(`${r}narration/subtitles.srt`)).text(),cues=[];
    for(const b of srt.split(/\n\n+/)){
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
}

async function init(){
  try{
    S.tasks=await fj(`${root}task-index.json`);
    taskSelect.innerHTML='';S.tasks.forEach(t=>{const o=document.createElement('option');o.value=t.path;o.textContent=t.label||t.path;taskSelect.appendChild(o);});
    const cur=S.tasks.find(t=>window.location.pathname.includes(t.path));
    await loadTask(cur?.path||S.tasks[0].path);
  }catch(e){document.body.innerHTML=`<div style="color:#fb7185;padding:40px">${e.message}</div>`;}
}

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
  stopPlay();S.selected=s;S.selLayer=null;
  document.querySelectorAll('.slide-btn').forEach(b=>b.classList.toggle('active',b.textContent.includes(`Slide ${pad(n)}`)));
  renderSlide();
}

function renderSlide(){
  const s=S.selected,meta=S.meta.get(s.slide)||s,tim=S.timing[sk(s.slide)]||{};
  const layers=meta.layers||[],skipped=layers.filter(l=>l.type==='key_point_card').length;
  slideLabel.textContent=sk(s.slide);slideTitleEl.textContent=`${layers.length} layers, ${skipped} skipped`;
  renderStats(s,layers);renderNarration(tim);renderPreview(s,layers);renderTimeline(s,meta,tim);renderLayers(s,meta);
  const nk = `notes-${S.taskPath}-${sk(s.slide)}`;
  notesEl.value = localStorage.getItem(nk)||'';
}

// ── Save notes (to overrides.json via the server, if running) ────────
notesEl.addEventListener('input',()=>{
  const nk = `notes-${S.taskPath}-${sk(S.selected.slide)}`;
  localStorage.setItem(nk, notesEl.value);
  fetch('/apply',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({task:S.taskPath,overrides:{[sk(S.selected.slide)]:{notes:notesEl.value}}})}).catch(()=>{});
});

function renderStats(s,layers){statsEl.innerHTML=`<dt>Canvas</dt><dd>${s.width}&times;${s.height}</dd><dt>Duration</dt><dd>${fmt(s.duration)}s</dd><dt>Layers</dt><dd>${layers.length}</dd>`;}

function renderNarration(tim){
  narrationEl.textContent=tim.script||'No narration.';
  if(tim.voiceover_file){audioEl.src=`${tRoot(S.taskPath)}${tim.voiceover_file}`;audioEl.hidden=false;}else{audioEl.removeAttribute('src');audioEl.hidden=true;}
}

function renderPreview(s,layers){
  const rt=tRoot(S.taskPath),key=sk(s.slide);
  const map={original:`${rt}output/${key}/original.png`,background:`${rt}output/${key}/background.png`,debug:`${rt}work_preview/element_debug/${key}_debug.jpg`,gallery:`${rt}work_preview/${key}_layer_gallery.jpg`};
  if(S.view!=='composite'){preview.style.display='none';assetPreview.style.display='block';assetPreview.src=map[S.view];return;}
  assetPreview.style.display='none';preview.style.display='block';preview.innerHTML='';
  const bg=document.createElement('img');bg.className='bg';bg.src=`${rt}output/${key}/background.png`;preview.appendChild(bg);
  const sub=document.createElement('div');sub.className='sub-overlay';sub.id='subOverlay';preview.appendChild(sub);
  let idx=0;
  for(const l of layers){
    if(l.type==='key_point_card'&&!S.showSkipped) continue;
    idx++;
    const anim=l.animation||'fade-in',dur=l.duration||.7;
    const wrap=document.createElement('div');
    wrap.style.position='absolute';wrap.style.left=`${l.x/s.width*100}%`;wrap.style.top=`${l.y/s.height*100}%`;
    wrap.style.width=`${l.width/s.width*100}%`;wrap.style.height=`${l.height/s.height*100}%`;wrap.style.zIndex=l.z_index;
    const img=document.createElement('img');
    img.className=`layer ${anim}${l.type==='key_point_card'?' skipped':''}`;
    img.style.width='100%';img.style.height='100%';
    img.dataset.start=l.start;img.dataset.layer=l.name;img.style.setProperty('--d',`${dur}s`);
    img.src=`${rt}output/${key}/${l.name}`;
    const num=document.createElement('div');num.className='layer-idx';num.textContent=idx;
    wrap.appendChild(img);wrap.appendChild(num);
    wrap.addEventListener('click',e=>{e.stopPropagation();selectLayer(l.name,idx);});
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
  let idx=0;
  for(const l of meta.layers||[]){
    idx++;
    const item=document.createElement('div');item.className='layer-item';item.dataset.layer=l.name;
    item.innerHTML=`<div style="display:flex;align-items:center;gap:6px"><span class="layer-num">${idx}</span><div><div class="layer-name">${l.name}</div><div class="layer-meta"><span>${fmt(l.start)}s</span><span>${fmt(l.duration)}s</span><span>${l.animation}</span><span>z${l.z_index}</span><span>${l.x},${l.y}</span><span>${l.width}&times;${l.height}</span></div></div><span class="pill">${l.type}</span></div>`;
    item.addEventListener('click',()=>selectLayer(l.name,idx));
    layerList.appendChild(item);
  }
}

function selectLayer(name,num){
  S.selLayer=name;
  document.querySelectorAll('.layer-item').forEach(e=>e.classList.toggle('active',e.dataset.layer===name));
  document.querySelectorAll('.stage-preview .layer').forEach(e=>e.classList.toggle('highlight',e.dataset.layer===name));
  document.querySelectorAll('.stage-preview .layer-idx').forEach(e=>{e.style.display='none'});
  if(name){const el=document.querySelector(`.stage-preview .layer.highlight`);if(el){const p=el.parentElement;if(p) p.querySelector('.layer-idx').style.display='flex';}}
}

// ── Play ────────────────────────────────────────────────────────────
function stopPlay(){S.timers.forEach(clearTimeout);S.timers=[];preview.classList.remove('playing');
  document.querySelectorAll('.stage-preview .layer').forEach(e=>e.classList.remove('show'));
  const s=document.getElementById('subOverlay');if(s)s.classList.remove('show');
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
showSkipped.addEventListener('change',e=>{S.showSkipped=e.target.checked;if(S.selected)renderPreview(S.selected,(S.meta.get(S.selected.slide)||S.selected).layers||[]);});

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

// ── Task / Theme ────────────────────────────────────────────────────
taskSelect.addEventListener('change',async e=>{taskSelect.disabled=true;await loadTask(e.target.value);taskSelect.disabled=false;});
const saved=localStorage.getItem('pui-theme')||'dark';document.documentElement.className=`theme-${saved}`;themeSelect.value=saved;
themeSelect.addEventListener('change',()=>{const t=themeSelect.value;document.documentElement.className=`theme-${t}`;localStorage.setItem('pui-theme',t);});
hfBtn.addEventListener('click',()=>{window.open(`${tRoot(S.taskPath)}hyperframes/index.html`,'_blank');});

init();
