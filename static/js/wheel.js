/* ── Spin Wheel ─────────────────────────────────────────────────────────── */
let _wheelPrizes  = [];
let _wheelSpinning = false;
const WC_W    = 110;
const WC_GAP  = 8;
const WC_SLOT = WC_W + WC_GAP;
const WC_REP  = 7;
const WC_ICON  = {coins:'💰', char:'🎭', thanks:'🙏'};
const WC_COLOR = {coins:'wc-coins', char:'wc-char', thanks:'wc-thanks'};
const WC_HISTORY_KEY = 'wheel_spin_history';
const WC_HISTORY_MAX = 10;

function _esc(s){ return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

/* ── History helpers ── */
function _loadHistory() {
  try { return JSON.parse(localStorage.getItem(WC_HISTORY_KEY) || '[]'); }
  catch(e) { return []; }
}
function _saveHistory(entries) {
  try { localStorage.setItem(WC_HISTORY_KEY, JSON.stringify(entries.slice(0, WC_HISTORY_MAX))); }
  catch(e) {}
}
function _pushHistory(prize, message) {
  const entries = _loadHistory();
  entries.unshift({
    label:   prize.label || '?',
    type:    prize.type  || '',
    message: message     || '',
    ts:      Date.now()
  });
  _saveHistory(entries);
}
function _fmtAgo(ts) {
  const s = Math.floor((Date.now() - ts) / 1000);
  if (s < 60)   return 'just now';
  if (s < 3600) return Math.floor(s/60) + 'm ago';
  if (s < 86400)return Math.floor(s/3600) + 'h ago';
  return Math.floor(s/86400) + 'd ago';
}
function renderWheelHistory() {
  const section = document.getElementById('wheelHistorySection');
  const list    = document.getElementById('wheelHistoryList');
  if (!section || !list) return;
  const entries = _loadHistory();
  if (!entries.length) { section.style.display = 'none'; return; }
  section.style.display = 'block';
  list.innerHTML = entries.map(e => `
    <div class="wh-hist-item">
      <span class="wh-hist-icon">${WC_ICON[e.type]||'🎁'}</span>
      <span class="wh-hist-lbl">${_esc(e.label)}</span>
      <span class="wh-hist-ago">${_fmtAgo(e.ts)}</span>
    </div>`).join('');
}

function buildWheelStrip(prizes) {
  const strip = document.getElementById('wheelStrip');
  if (!strip) return;
  if (!prizes || !prizes.length) {
    strip.innerHTML = '<div style="padding:20px 40px;color:var(--text3);white-space:nowrap;font-size:.85rem;">No prizes yet</div>';
    return;
  }
  let html = '';
  for (let r = 0; r < WC_REP; r++) {
    prizes.forEach((p, i) => {
      html += `<div class="wheel-card ${WC_COLOR[p.type]||''}" data-r="${r}" data-i="${i}">
        <div class="wheel-card-icon">${WC_ICON[p.type]||'🎁'}</div>
        <div class="wheel-card-lbl">${_esc(p.label)}</div>
      </div>`;
    });
  }
  strip.style.transition = 'none';
  strip.style.transform  = 'translateX(0)';
  strip.innerHTML = html;
}

function renderPrizesPreview(prizes) {
  const grid = document.getElementById('wheelPrizesGrid');
  if (!grid) return;
  if (!prizes || !prizes.length) { grid.innerHTML = '<div style="color:var(--text3);font-size:.8rem;grid-column:1/-1;">No prizes configured.</div>'; return; }
  grid.innerHTML = prizes.map(p => `
    <div class="prize-prev-item">
      <div class="prize-prev-icon">${WC_ICON[p.type]||'🎁'}</div>
      <div class="prize-prev-lbl">${_esc(p.label)}</div>
      <div class="prize-prev-pct">${p.percent||0}%</div>
    </div>`).join('');
}

async function openWheelModal() {
  try {
    _wheelSpinning = false;
    const errEl = document.getElementById('wheelCodeError');
    const btn   = document.getElementById('wheelSpinBtn');
    const inp   = document.getElementById('wheelCodeInput');
    if (errEl) errEl.style.display = 'none';
    if (btn)   { btn.disabled = false; btn.textContent = '🎰 Spin'; }
    if (inp)   inp.value = '';
    showModal('wheelModal');
    renderWheelHistory();
    const data = await apiGet('/api/wheel/prizes');
    _wheelPrizes = (data && data.prizes) ? data.prizes : [];
    buildWheelStrip(_wheelPrizes);
    renderPrizesPreview(_wheelPrizes);
  } catch(e) {
    console.error('[Wheel] openWheelModal error:', e);
  }
}

window.openWheelModal = openWheelModal;
document.getElementById('openWheelBtn')?.addEventListener('click', openWheelModal);

document.getElementById('wheelSpinBtn')?.addEventListener('click', async () => {
  if (_wheelSpinning) return;
  const inp   = document.getElementById('wheelCodeInput');
  const errEl = document.getElementById('wheelCodeError');
  const btn   = document.getElementById('wheelSpinBtn');
  const code  = (inp.value||'').trim().toUpperCase();
  errEl.style.display = 'none';

  if (!code) {
    errEl.textContent = '⚠️ Enter a secret code first';
    errEl.style.display = 'block'; return;
  }
  if (!_wheelPrizes.length) {
    errEl.textContent = '⚠️ No prizes configured';
    errEl.style.display = 'block'; return;
  }

  btn.disabled = true; btn.textContent = '⏳ Spinning…'; _wheelSpinning = true;
  const data = await apiPost('/api/wheel/spin', {code});

  if (!data.ok) {
    errEl.textContent = data.error || 'Error occurred';
    errEl.style.display = 'block';
    btn.disabled = false; btn.textContent = '🎰 Spin'; _wheelSpinning = false;
    return;
  }

  buildWheelStrip(_wheelPrizes);
  const winIdx = data.winner_idx;
  const winPos = (WC_REP - 2) * _wheelPrizes.length + winIdx;
  const outer  = document.getElementById('wheelStripOuter');
  const contW  = outer.offsetWidth;
  const offset = winPos * WC_SLOT - Math.floor((contW - WC_W) / 2);
  const strip  = document.getElementById('wheelStrip');

  requestAnimationFrame(() => requestAnimationFrame(() => {
    strip.style.transition = 'transform 4.5s cubic-bezier(0.12, 0.8, 0.15, 1)';
    strip.style.transform  = `translateX(-${Math.max(0, offset)}px)`;
  }));

  setTimeout(() => {
    strip.querySelectorAll('.wheel-card').forEach(c => c.classList.remove('winner'));
    const cards = strip.querySelectorAll('.wheel-card');
    if (cards[winPos]) cards[winPos].classList.add('winner');
    _pushHistory(data.prize, data.message);
    renderWheelHistory();
    showToast(data.message || '🎉 Congratulations!', 'success', 5500);
    setTimeout(() => {
      btn.disabled = false; btn.textContent = '🎰 Spin';
      _wheelSpinning = false; inp.value = '';
    }, 2500);
  }, 4700);
});
