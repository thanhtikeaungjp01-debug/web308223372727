/* User-controlled welcome slides; no autoplay or extra network requests. */
(() => {
  const track = document.getElementById('miniBannerTrack');
  const buttons = [...document.querySelectorAll('[data-slide]')];
  if (!track || buttons.length < 2) return;
  buttons.forEach((button, index) => button.addEventListener('click', () => {
    track.scrollTo({ left: index * track.clientWidth, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth' });
  }));
  track.addEventListener('scroll', () => {
    const active = Math.round(track.scrollLeft / track.clientWidth);
    buttons.forEach((button, index) => button.setAttribute('aria-pressed', String(index === active)));
  }, { passive: true });
})();
