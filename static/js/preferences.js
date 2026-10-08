/* Shared display preferences, with optional Telegram fullscreen controls. */
(() => {
  const root = document.documentElement;
  const themes = [...document.querySelectorAll('.theme-choice')];
  const sizes = [...document.querySelectorAll('.size-choice')];
  const status = document.getElementById('displayStatus');
  const tg = window.Telegram?.WebApp;
  const inTelegram = Boolean(tg?.initData);
  const supportsFullscreen = inTelegram && tg.isVersionAtLeast?.('8.0') &&
    typeof tg.requestFullscreen === 'function' && typeof tg.exitFullscreen === 'function';
  const save = (key, value) => { try { localStorage.setItem(key, value); } catch (_) {} };
  const announce = (message) => { if (status) status.textContent = message; };

  function setTheme(value) {
    const theme = ['dark', 'light', 'blue'].includes(value) ? value : 'dark';
    root.dataset.theme = theme;
    save('waifu-theme', theme);
    themes.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.theme === theme)));
    const color = { dark: '#10101c', light: '#f4eef9', blue: '#080f22' }[theme];
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', color);
    if (inTelegram && tg.isVersionAtLeast?.('6.1')) {
      try { tg.setHeaderColor(color); tg.setBackgroundColor(color); } catch (_) {}
    }
  }

  function setSize(value) {
    const size = value === 'large' ? 'large' : 'small';
    root.dataset.appSize = size;
    save('waifu-app-size', size);
    sizes.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.appSize === size)));
  }

  setTheme(root.dataset.theme);
  setSize(inTelegram && tg.isFullscreen ? 'large' : root.dataset.appSize);
  // Restore layout without requesting fullscreen before a user gesture.
  if (inTelegram && root.dataset.appSize === 'large') tg.expand();
  themes.forEach(button => button.addEventListener('click', () => setTheme(button.dataset.theme)));
  sizes.forEach(button => button.addEventListener('click', () => {
    const size = button.dataset.appSize;
    setSize(size);
    announce('');
    if (!inTelegram) return;
    try {
      if (size === 'large') {
        tg.expand();
        if (supportsFullscreen && !tg.isFullscreen) tg.requestFullscreen();
      } else if (supportsFullscreen && tg.isFullscreen) {
        tg.exitFullscreen();
      } else {
        // Telegram exposes expand(), but no programmatic collapse() API.
        announce('Small layout applied. Swipe down to reduce the Telegram window.');
      }
    } catch (_) {
      announce('Layout updated. Fullscreen is unavailable on this device.');
    }
  }));
  if (supportsFullscreen) {
    tg.onEvent('fullscreenChanged', () => {
      setSize(tg.isFullscreen ? 'large' : 'small');
      announce('');
    });
    tg.onEvent('fullscreenFailed', () => announce('Layout updated. Fullscreen is unavailable on this device.'));
  }
})();
