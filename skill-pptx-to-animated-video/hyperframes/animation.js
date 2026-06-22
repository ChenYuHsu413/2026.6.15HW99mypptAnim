// Draft preview -- reads the same composition.json the renderer consumes,
// so the preview can't drift from the final MP4.
const slideRoot = document.getElementById('slide');
const caption = document.getElementById('caption');
const playButton = document.getElementById('play');
const subtitleButton = document.getElementById('subtitles');
let subtitlesOn = true;

subtitleButton.addEventListener('click', () => {
  subtitlesOn = !subtitlesOn;
  subtitleButton.textContent = subtitlesOn ? 'Subtitles On' : 'Subtitles Off';
  caption.classList.toggle('on', subtitlesOn);
});

const sleep = ms => new Promise(r => setTimeout(r, ms));

async function loadComposition() {
  const res = await fetch('../composition.json');
  if (!res.ok) throw new Error(res.status);
  return res.json();
}

function showSlide(comp, slide) {
  slideRoot.innerHTML = '';
  const base = document.createElement('img');
  base.className = 'bg';
  base.src = `../${slide.background}`;
  slideRoot.appendChild(base);
  for (const layer of slide.layers) {
    if (layer.hidden) continue;
    const img = document.createElement('img');
    img.className = `layer ${layer.enter.type}`;
    img.src = `../${layer.image}`;
    img.style.left = `${layer.bbox[0] / comp.canvas.width * 100}%`;
    img.style.top = `${layer.bbox[1] / comp.canvas.height * 100}%`;
    img.style.width = `${layer.bbox[2] / comp.canvas.width * 100}%`;
    img.style.height = `${layer.bbox[3] / comp.canvas.height * 100}%`;
    img.style.zIndex = layer.z;
    img.style.setProperty('--dur', `${layer.duration}s`);
    slideRoot.appendChild(img);
    window.setTimeout(() => img.classList.add('show'), layer.start * 1000);
  }
  caption.textContent = slide.narration;
  caption.classList.toggle('on', subtitlesOn);
}

async function play() {
  playButton.disabled = true;
  const comp = await loadComposition();
  for (const slide of comp.slides) {
    showSlide(comp, slide);
    const audio = new Audio(`../${slide.audio}`);
    try { await audio.play(); } catch (e) {}
    await sleep(slide.duration * 1000 + 500);
  }
  playButton.disabled = false;
}

playButton.addEventListener('click', play);
loadComposition()
  .then(c => showSlide(c, c.slides[0]))
  .catch(() => { caption.textContent = 'composition.json not found -- run build_composition.py'; caption.classList.add('on'); });
