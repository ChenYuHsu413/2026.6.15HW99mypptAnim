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
const pad = n => String(n).padStart(2, '0');

async function loadProject() {
  const res = await fetch('project.json');
  return res.json();
}

function showSlide(slide, timing) {
  slideRoot.innerHTML = '';
  const base = document.createElement('img');
  base.className = 'bg';
  base.src = `../output/slide_${pad(slide.slide)}/background.png`;
  slideRoot.appendChild(base);
  for (const layer of slide.layers) {
    const img = document.createElement('img');
    img.className = `layer ${layer.animation}`;
    img.src = `../output/slide_${pad(slide.slide)}/${layer.name}`;
    img.style.left = `${layer.x / slide.width * 100}%`;
    img.style.top = `${layer.y / slide.height * 100}%`;
    img.style.width = `${layer.width / slide.width * 100}%`;
    img.style.height = `${layer.height / slide.height * 100}%`;
    img.style.zIndex = layer.z_index;
    img.style.setProperty('--dur', `${layer.duration}s`);
    slideRoot.appendChild(img);
    window.setTimeout(() => img.classList.add('show'), layer.start * 1000);
  }
  caption.textContent = timing.script;
  caption.classList.toggle('on', subtitlesOn);
}

async function play() {
  playButton.disabled = true;
  const project = await loadProject();
  for (const slide of project.slides) {
    const timing = project.timing[`slide_${pad(slide.slide)}`];
    showSlide(slide, timing);
    const audio = new Audio(`../audio/slide_${pad(slide.slide)}_voiceover.mp3`);
    try { await audio.play(); } catch (e) {}
    await sleep(slide.duration * 1000 + 500);
  }
  playButton.disabled = false;
}

playButton.addEventListener('click', play);
loadProject().then(p => showSlide(p.slides[0], p.timing[`slide_${pad(p.slides[0].slide)}`]));
