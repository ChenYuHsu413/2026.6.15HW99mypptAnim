const slideEl = document.getElementById('slide');
const caption = document.getElementById('caption');
const playBtn = document.getElementById('playBtn');
const slideSelect = document.getElementById('slideSelect');
const progress = document.getElementById('progress').querySelector('.fill');

const pad = n => String(n).padStart(2, '0');
const root = '.';
let project = null;
let timing = null;
let currentSlide = null;
let timers = [];
let playing = false;

async function init() {
  project = await (await fetch(`${root}/hyperframes/project.json`)).json();
  timing = await (await fetch(`${root}/narration/narration_timing.json`)).json();
  project.slides.forEach(s => {
    const opt = document.createElement('option');
    opt.value = s.slide;
    opt.textContent = `Slide ${pad(s.slide)}`;
    slideSelect.appendChild(opt);
  });
  slideSelect.addEventListener('change', () => showSlide(Number(slideSelect.value)));
  showSlide(project.slides[0].slide);
}

function showSlide(num) {
  stop();
  currentSlide = project.slides.find(s => s.slide === num);
  if (!currentSlide) return;
  slideEl.innerHTML = '';

  const key = `slide_${pad(num)}`;
  const bg = document.createElement('img');
  bg.className = 'bg';
  bg.src = `${root}/output/${key}/background.png`;
  slideEl.appendChild(bg);

  for (const l of currentSlide.layers) {
    const img = document.createElement('img');
    img.className = `layer ${l.animation}`;
    img.dataset.start = l.start;
    img.style.setProperty('--d', `${l.duration}s`);
    img.style.left = `${l.x / currentSlide.width * 100}%`;
    img.style.top = `${l.y / currentSlide.height * 100}%`;
    img.style.width = `${l.width / currentSlide.width * 100}%`;
    img.style.height = `${l.height / currentSlide.height * 100}%`;
    img.style.zIndex = l.z_index;
    img.src = `${root}/output/${key}/${l.name}`;
    slideEl.appendChild(img);
  }
}

playBtn.addEventListener('click', () => {
  if (playing) { stop(); return; }
  if (!currentSlide) return;
  playing = true;
  slideEl.classList.add('playing');
  playBtn.classList.add('playing');
  playBtn.textContent = 'Stop';
  progress.style.width = '0%';

  const key = `slide_${pad(currentSlide.slide)}`;
  const dur = currentSlide.duration;
  const layers = Array.from(slideEl.querySelectorAll('.layer'));
  timers = [];

  // Layer reveals
  layers.forEach(el => {
    const start = parseFloat(el.dataset.start);
    const t = setTimeout(() => el.classList.add('show'), start * 1000);
    timers.push(t);
  });

  // Audio
  const tInfo = timing[key];
  if (tInfo?.voiceover_file) {
    const audio = new Audio(`${root}/${tInfo.voiceover_file}`);
    audio.play().catch(() => {});
    timers.push(setTimeout(() => audio.pause(), dur * 1000 + 1000));
  }

  // Progress bar
  const startTime = Date.now();
  const progInterval = setInterval(() => {
    const pct = Math.min(100, (Date.now() - startTime) / (dur * 1000) * 100);
    progress.style.width = `${pct}%`;
    if (pct >= 100) clearInterval(progInterval);
  }, 100);
  timers.push(setInterval(() => {}, 0)); // dummy so timers array tracks it
  timers.push(() => clearInterval(progInterval));

  // Auto-stop
  const endTimer = setTimeout(() => stop(), dur * 1000 + 500);
  timers.push(endTimer);
});

function stop() {
  playing = false;
  timers.forEach(t => clearTimeout(t));
  timers = [];
  slideEl.classList.remove('playing');
  playBtn.classList.remove('playing');
  playBtn.textContent = 'Play';
  progress.style.width = '0%';
  document.querySelectorAll('#slide .layer').forEach(el => el.classList.remove('show'));
  caption.classList.remove('show');
}
