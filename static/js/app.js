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
  return nativeFetch(input, options);
};

/* ── Telegram Mini App init ──────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', function () {
  const tg = window.Telegram?.WebApp;
  if (!tg || !tg.initData) return;          // not inside Telegram — skip

  tg.ready();

  // Already logged in → nothing to do
  const metaLoggedIn = document.querySelector('meta[name="tg-logged-in"]');
  if (metaLoggedIn && metaLoggedIn.content === '1') return;

  // Auto-login using initData (HMAC validated server-side)
  fetch('/auth/webapp', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ initData: tg.initData, next: new URLSearchParams(location.search).get('next') || location.pathname }),
    signal: AbortSignal.timeout(15000),
  })
    .then(r => r.json())
    .then(d => {
      if (d.ok) window.location.href = d.redirect || '/';
      else reportLoginError(d.error || 'Unable to sign in. Please reopen the app from Telegram.');
    })
    .catch(() => reportLoginError('Unable to connect. Please reopen the app from Telegram.'));
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
