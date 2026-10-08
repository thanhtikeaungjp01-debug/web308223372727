/* All same-origin writes carry the session's CSRF token, including uploads. */
const nativeFetch = window.fetch.bind(window);
window.fetch = (input, options = {}) => {
  const url = new URL(input instanceof Request ? input.url : input, location.href);
  const method = (options.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
  if (url.origin === location.origin && !['GET', 'HEAD', 'OPTIONS'].includes(method)) {
    const headers = new Headers(options.headers || (input instanceof Request ? input.headers : undefined));
    const token = document.querySelector('meta[name="csrf-token"]')?.content;
    if (token) headers.set('X-CSRF-Token', token);
    options = { ...options, headers };
  }
  return nativeFetch(input, options).then(async response => {
    if (url.origin === location.origin && response.status === 403 && response.headers.get('Content-Type')?.includes('application/json')) {
      const payload = await response.clone().json().catch(() => ({}));
      if (payload.banned) window.AppBoot?.ban();
    }
    return response;
  });
};

/* ── Telegram Mini App init ──────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', async function () {
  const tg = window.Telegram?.WebApp;
  const loggedIn = document.querySelector('meta[name="tg-logged-in"]')?.content === '1';
  if (tg?.initData) tg.ready();
  if (tg?.initData && !loggedIn) {
    try {
      const response = await fetch('/auth/webapp', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body:JSON.stringify({initData:tg.initData,next:new URLSearchParams(location.search).get('next')||location.pathname}),
        signal:AbortSignal.timeout(15000)
      });
      const result = await response.json();
      if (result.banned) return;
      if (!result.ok) throw new Error(result.error || 'Unable to sign in. Reopen the app from Telegram.');
      location.replace(result.redirect || '/');
      return;
    } catch (error) { window.AppBoot?.fail(error.message); return; }
  }
  if (loggedIn && document.getElementById('miniBalanceValue')) {
    try {
      const response = await fetch('/api/profile', {signal:AbortSignal.timeout(15000)});
      const result = await response.json();
      if (result.banned) return;
      if (!response.ok || !result.ok) throw new Error(result.error || 'Could not load your profile.');
      document.getElementById('miniBalanceValue').textContent = result.balance;
      document.getElementById('miniDisplayName').textContent = result.first_name;
      const avatar = document.querySelector('.mini-avatar');
      if (result.photo_url && avatar) {
        const picture = avatar.querySelector('img') || document.createElement('img');
        picture.width=64; picture.height=64; picture.alt=''; picture.decoding='async';
        picture.onerror=()=>picture.remove();
        picture.src=result.photo_url;
        if (!picture.parentNode) avatar.append(picture);
      }
      document.querySelector('.mini-balance')?.setAttribute('aria-label', 'Wallet balance ' + result.balance);
    } catch(error) { window.AppBoot?.fail(error.message); return; }
  }
  window.AppBoot?.finish();
});

function reportLoginError(message) {
  const status = document.getElementById('auth-status');
  if (status) status.textContent = message;
  else showToast(message, 'error', 8000);
}

/* ── Bot status dot ──────────────────────────────────────────────────────── */
(function () {
  const dot = document.getElementById('botStatusDot');
  if (!dot) return;
  function check() {
    if (document.hidden) return;
    fetch('/bot-status')
      .then(r => r.json())
      .then(d => {
        dot.classList.remove('online', 'offline');
        if (d.online) {
          dot.classList.add('online');
          dot.title = 'Bot: online';
        } else {
          dot.classList.add('offline');
          dot.title = d.reason === 'not_configured' ? 'Bot: not configured' : 'Bot: offline';
        }
      })
      .catch(() => {
        dot.classList.remove('online', 'offline');
        dot.classList.add('offline');
        dot.title = 'Bot: offline';
      });
  }
  check();
  setInterval(check, 60000);
})();

/* ── Mobile nav toggle ───────────────────────────────────────────────────── */
const navToggle  = document.getElementById('navToggle');
const mobileMenu = document.getElementById('mobileMenu');
if (navToggle && mobileMenu) {
  const setMenuOpen = (open) => {
    mobileMenu.classList.toggle('open', open);
    navToggle.setAttribute('aria-expanded', String(open));
    navToggle.setAttribute('aria-label', open ? 'Close main menu' : 'Open main menu');
    mobileMenu.setAttribute('aria-hidden', String(!open));
    mobileMenu.inert = !open;
  };
  navToggle.addEventListener('click', () => {
    setMenuOpen(!mobileMenu.classList.contains('open'));
  });
  mobileMenu.querySelectorAll('.mobile-link').forEach(link => {
    link.addEventListener('click', (event) => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      setMenuOpen(false);
    });
  });
  document.addEventListener('click', e => {
    if (!navToggle.contains(e.target) && !mobileMenu.contains(e.target)) {
      setMenuOpen(false);
    }
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && mobileMenu.classList.contains('open')) { setMenuOpen(false); navToggle.focus(); }
  });
}

/* ── Toast notifications ─────────────────────────────────────────────────── */
function showToast(msg, type = 'info', duration = 3500) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = '0';
    setTimeout(() => toast.remove(), 350);
  }, duration);
}

/* ── Modal helpers ───────────────────────────────────────────────────────── */
let activeModal = null;
let modalOpener = null;
function showModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  modalOpener = document.activeElement;
  activeModal = el;
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  el.setAttribute('aria-label', el.querySelector('.modal-title')?.textContent || 'Confirm action');
  el.style.display = 'flex';
  document.body.style.overflow = 'hidden';
  (el.querySelector('button, input, select, [tabindex="0"]') || el).focus();
}
function closeModal(id) {
  const el = document.getElementById(id);
  if (!el) return;
  el.style.display = 'none';
  document.body.style.overflow = '';
  activeModal = null;
  if (modalOpener?.isConnected) modalOpener.focus();
}

document.addEventListener('click', e => {
  if (e.target.classList.contains('modal-overlay')) closeModal(e.target.id);
});
document.addEventListener('keydown', e => {
  if (!activeModal) return;
  if (e.key === 'Escape') closeModal(activeModal.id);
  if (e.key === 'Tab') {
    const controls = [...activeModal.querySelectorAll('button:not(:disabled), input:not(:disabled), select:not(:disabled), a[href], [tabindex="0"]')].filter(el => el.getClientRects().length);
    const first = controls[0], last = controls[controls.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last?.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first?.focus(); }
  }
});

/* ── Double-click / rapid-click lock ─────────────────────────────────────── *
 * Usage:
 *   btn.addEventListener('click', () => withLock(btn, async () => {
 *     // only runs if not already in progress
 *   }));
 * ─────────────────────────────────────────────────────────────────────────── */
async function withLock(btnOrKey, fn) {
  const key     = typeof btnOrKey === 'string' ? btnOrKey : btnOrKey;
  const btn     = typeof btnOrKey === 'object' ? btnOrKey : null;
  const dataKey = '__locked__';

  if (btn) {
    if (btn.dataset[dataKey]) return;      // already running → ignore click
    btn.dataset[dataKey] = '1';
    const origText = btn.textContent;
    const origDisabled = btn.disabled;
    btn.disabled = true;
    try {
      await fn({ origText, btn });
    } finally {
      delete btn.dataset[dataKey];
      btn.disabled = origDisabled;
    }
  } else {
    await fn({});
  }
}

/* ── API helpers ─────────────────────────────────────────────────────────── */
async function apiRequest(url, options = {}) {
  try {
    const res = await fetch(url, { ...options, signal: AbortSignal.timeout(20000) });
    const json = res.headers.get('Content-Type')?.includes('application/json');
    const data = json ? await res.json() : null;
    if (res.status === 401) return { ok: false, error: 'Please sign in with Telegram to continue.' };
    if (!res.ok) return { ok: false, error: data?.error || `Unable to complete the request (${res.status}). Please try again.` };
    return data || { ok: false, error: 'Unexpected response. Please refresh the page.' };
  } catch (err) {
    return { ok: false, error: err.name === 'TimeoutError'
      ? 'The request timed out. Check your balance or activity before trying again.'
      : 'Connection lost. Check your activity before retrying a purchase or transfer.' };
  }
}

async function apiPost(url, body = {}) {
  return apiRequest(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
}

async function apiGet(url) { return apiRequest(url); }

/* ── Char images: shimmer skeleton + fade-in ─────────────────────────────── */
function _imgReady(img) {
  img.classList.add('img-ready');
  const wrap = img.closest('.char-img-wrap');
  if (wrap) wrap.classList.add('loaded');
}
document.querySelectorAll('.char-img, .table-char-img').forEach(img => {
  if (img.complete && img.naturalWidth) {
    _imgReady(img);
  } else {
    img.addEventListener('load',  () => _imgReady(img));
    img.addEventListener('error', () => _imgReady(img));
  }
});
document.querySelectorAll('video.char-video').forEach(video => {
  const shell = document.createElement('div');
  shell.className = 'video-shell';
  video.before(shell); shell.append(video);
  video.classList.add('img-ready');
  video.closest('.char-img-wrap')?.classList.add('loaded');
  video.preload = 'none'; video.controls = false;
  if (!video.poster) video.poster = '/static/img/card-placeholder.svg';
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'video-play';
  button.textContent = '▶ Play video · 480p';
  shell.append(button);
  const failed = () => { button.hidden=false; button.disabled=false; button.textContent='↻ Video unavailable · Tap to retry'; };
  video.addEventListener('error', failed);
  video.addEventListener('playing', () => { button.hidden=true; });
  button.addEventListener('click', () => {
    button.disabled=true; button.textContent='🌸 Preparing video…';
    video.controls=true;
    const source = new URL(video.dataset.videoSrc, location.href);
    // Telegram's WebViews support MP4; Chromium builds without H.264 use WebM.
    if (!video.canPlayType('video/mp4; codecs="avc1.42E01E"')) source.searchParams.set('format', 'webm');
    video.src=source.href;
    video.load();
    video.play().catch(failed);
  });
});

/* Ads load only when visible, and stay unloaded after dismissal. */
(() => {
  const banner = document.getElementById('adBanner');
  if (!banner) return;
  const media = banner.querySelector('[data-src]');
  const key = 'waifu-ad-dismissed:' + banner.dataset.adKey;
  const hide = () => {
    banner.hidden = true;
    if (media instanceof HTMLVideoElement) { media.pause(); media.removeAttribute('src'); media.load(); }
  };
  try { if (sessionStorage.getItem(key) === '1') hide(); } catch (_) {}
  document.getElementById('adBannerClose')?.addEventListener('click', () => {
    hide();
    try { sessionStorage.setItem(key, '1'); } catch (_) {}
    document.getElementById('searchInput')?.focus({ preventScroll: true });
  });
  if (banner.hidden || !media) return;
  media.addEventListener('error', hide, { once: true });
  const load = () => { if (!banner.hidden) media.src = media.dataset.src; };
  if ('IntersectionObserver' in window) {
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { observer.disconnect(); load(); }
    }, { rootMargin: '100px' });
    observer.observe(banner);
  } else load();
})();

/* Immediate feedback while the browser loads a fresh, authenticated page. */
(() => {
  const root = document.documentElement;
  document.addEventListener('click', event => {
    const link = event.target.closest('a[href]');
    if (!link || event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || link.target || link.hasAttribute('download')) return;
    const url = new URL(link.href, location.href);
    if (url.origin === location.origin && (url.pathname !== location.pathname || url.search !== location.search)) root.classList.add('page-loading');
  });
  window.addEventListener('pageshow', () => root.classList.remove('page-loading'));
})();
