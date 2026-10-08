/* ── Telegram Mini App init ──────────────────────────────────────────────── */
(function () {
  const tg = window.Telegram?.WebApp;
  if (!tg || !tg.initData) return;          // not inside Telegram — skip

  tg.ready();
  tg.expand();

  // Already logged in → nothing to do
  const metaLoggedIn = document.querySelector('meta[name="tg-logged-in"]');
  if (metaLoggedIn && metaLoggedIn.content === '1') return;

  // Auto-login using initData (HMAC validated server-side)
  fetch('/auth/webapp', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ initData: tg.initData }),
  })
    .then(r => r.json())
    .then(d => { if (d.ok) window.location.href = d.redirect || '/market'; })
    .catch(() => {});
})();

/* ── Bot status dot ──────────────────────────────────────────────────────── */
(function () {
  const dot = document.getElementById('botStatusDot');
  if (!dot) return;
  function check() {
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

/* ── Page loader ─────────────────────────────────────────────────────────── */
function _hideLoader() {
  const loader = document.getElementById('page-loader');
  if (loader && !loader.classList.contains('hidden')) {
    loader.classList.add('hidden');
  }
}
// Hide as soon as DOM + scripts are ready (don't wait for slow images)
document.addEventListener('DOMContentLoaded', _hideLoader);
// Absolute fallback: force-hide after 3 s no matter what
setTimeout(_hideLoader, 3000);
// Also honour the original load event if it fires sooner
window.addEventListener('load', _hideLoader);
window.addEventListener('pageshow', _hideLoader);

/* ── Fast internal navigation prefetch ───────────────────────────────────── */
(() => {
  const prefetched = new Set();
  const warm = (link) => {
    if (!link || !link.href || link.target || link.origin !== location.origin) return;
    if (prefetched.has(link.href)) return;
    const path = new URL(link.href).pathname;
    if (!['/', '/market', '/auction', '/wallet', '/admin', '/rocket', '/harem'].includes(path)) return;
    prefetched.add(link.href);
    const hint = document.createElement('link');
    hint.rel = 'prefetch';
    hint.as = 'document';
    hint.href = link.href;
    document.head.appendChild(hint);
  };
  document.querySelectorAll('a[href]').forEach(link => {
    link.addEventListener('pointerenter', () => warm(link), { once: true, passive: true });
    link.addEventListener('touchstart', () => warm(link), { once: true, passive: true });
  });
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
  };
  navToggle.addEventListener('click', () => {
    setMenuOpen(!mobileMenu.classList.contains('open'));
  });
  mobileMenu.querySelectorAll('.mobile-link').forEach(link => {
    link.addEventListener('click', (event) => {
      // Keep navigation reliable inside Telegram Mini App/WebView: close the
      // drawer, then explicitly navigate to the anchor target.
      event.stopPropagation();
      const target = link.href;
      setMenuOpen(false);
      if (target) window.location.assign(target);
    });
  });
  document.addEventListener('click', e => {
    if (!navToggle.contains(e.target) && !mobileMenu.contains(e.target)) {
      setMenuOpen(false);
    }
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape') setMenuOpen(false);
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
function showModal(id) {
  const el = document.getElementById(id);
  if (el) { el.style.display = 'flex'; document.body.style.overflow = 'hidden'; }
}
function closeModal(id) {
  const el = document.getElementById(id);
  if (el) { el.style.display = 'none'; document.body.style.overflow = ''; }
}

document.addEventListener('click', e => {
  if (e.target.classList.contains('modal-overlay')) {
    e.target.style.display = 'none';
    document.body.style.overflow = '';
  }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay').forEach(m => {
      m.style.display = 'none';
    });
    document.body.style.overflow = '';
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
async function apiPost(url, body = {}) {
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (res.status === 401) return { ok: false, error: 'Not logged in — please refresh and login again.' };
    if (res.status === 403) return { ok: false, error: 'Permission denied.' };
    if (!res.ok && res.status >= 500) return { ok: false, error: `Server error (${res.status})` };
    return await res.json();
  } catch (err) {
    console.error('apiPost error:', err);
    return { ok: false, error: 'Network error — check your connection.' };
  }
}

async function apiGet(url) {
  try {
    const res = await fetch(url);
    if (!res.ok) return { ok: false, error: `HTTP ${res.status}` };
    return await res.json();
  } catch (err) {
    return { ok: false, error: 'Network error' };
  }
}

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
  const markVideoReady = () => {
    video.classList.add('img-ready');
    const wrap = video.closest('.char-img-wrap');
    if (wrap) wrap.classList.add('loaded');
  };
  if (video.readyState >= 2) markVideoReady();
  else {
    video.addEventListener('loadeddata', markVideoReady, { once: true });
    video.addEventListener('error', markVideoReady, { once: true });
  }
});

/* ── Ad banner close ─────────────────────────────────────────────────────── */
const adCloseBtn = document.getElementById('adBannerClose');
if (adCloseBtn) {
  adCloseBtn.addEventListener('click', () => {
    const banner = document.getElementById('adBanner');
    if (banner) banner.style.display = 'none';
    sessionStorage.setItem('ad_closed', '1');
  });
  // Restore closed state within the session
  if (sessionStorage.getItem('ad_closed')) {
    const banner = document.getElementById('adBanner');
    if (banner) banner.style.display = 'none';
  }
}
