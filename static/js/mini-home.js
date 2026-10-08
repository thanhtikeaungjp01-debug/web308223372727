/* Welcome photos slide automatically every three seconds. */
(() => {
  const track = document.getElementById('miniBannerTrack');
  if (!track || track.children.length < 2) return;
  const slides = [...track.children];
  const dots = [...document.querySelectorAll('[data-slide]')];
  const motion = matchMedia('(prefers-reduced-motion: reduce)');
  let active = 0;
  let visible = true;
  let timer;

  const prepareNext = () => {
    const image = slides[(active + 1) % slides.length].querySelector('img');
    if (image) image.loading = 'eager';
  };
  const moveTo = (index, behavior = motion.matches ? 'instant' : 'smooth') => {
    active = index;
    track.scrollTo({ left: index * track.clientWidth, behavior });
    dots.forEach((dot, i) => dot.setAttribute('data-active', String(i === index)));
    slides.forEach((slide, i) => slide.tabIndex = i === index ? 0 : -1);
    prepareNext();
  };
  const schedule = () => {
    clearTimeout(timer);
    if (document.hidden || !visible) return;
    timer = setTimeout(() => {
      moveTo((active + 1) % slides.length);
      schedule();
    }, 3000);
  };
  document.addEventListener('visibilitychange', schedule);
  window.addEventListener('resize', () => moveTo(active, 'instant'));
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(([entry]) => { visible = entry.isIntersecting; schedule(); })
      .observe(track);
  }
  moveTo(0, 'instant');
  schedule();
})();
