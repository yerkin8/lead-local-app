// Things that differ between the desktop window and Telegram: links, confirm, back button,
// haptics, theme and which features exist. Load after transport.js.
(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else if (root.LEM) root.LEM.platform = api;
})(typeof window !== 'undefined' ? window : globalThis, function (root) {
  const tg = () => root.Telegram?.WebApp;
  const kind = () => (root.LEM ? root.LEM.kind : 'desktop');

  // Features that need the PC (files, folders, dialogs). Web counterparts come in phases 3 and 5.
  const DESKTOP_FEATURES = new Set(['files', 'logs', 'backup', 'import', 'export', 'media', 'refresh']);

  function safeUrl(url) {
    let parsed;
    try { parsed = new URL(String(url)); } catch (_) { throw Error('Некорректная ссылка'); }
    if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password) {
      throw Error('Можно открыть только обычную веб-ссылку');
    }
    return parsed;
  }

  // A chat link longer than this cannot be opened (Windows/desktop limit, KNOWN_ISSUES K4).
  const MAX_LINK = 2048;
  const CHAT_HOSTS = new Set(['t.me', 'wa.me', 'api.whatsapp.com']);

  // Long message: open the chat without the text and put the text on the clipboard instead.
  async function openLongChat(parsed) {
    const text = parsed.searchParams.get('text') || '';
    parsed.searchParams.delete('text');
    let copied = false;
    try { await root.navigator.clipboard.writeText(text); copied = true; } catch (_) {
      try { copied = typeof root.copyFallback === 'function' && root.copyFallback(text); } catch (_) { copied = false; }
    }
    const result = await root.LEM.invoke('open_external', [parsed.href]);
    if (typeof root.toast === 'function') {
      root.toast(copied ? 'Текст длинный — он скопирован. Вставьте его в чат: Ctrl+V'
        : 'Текст длинный — скопируйте его кнопкой «Копировать» и вставьте в чат', !copied);
    }
    return result;
  }

  return {
    can: (feature) => kind() === 'desktop' || !DESKTOP_FEATURES.has(feature),
    isTelegram: () => kind() === 'web' && Boolean(tg()?.initData),
    openLink(url) {
      const parsed = safeUrl(url);
      const app = tg();
      if (kind() !== 'web') {
        if (parsed.href.length > MAX_LINK && CHAT_HOSTS.has(parsed.hostname) && parsed.searchParams.has('text')) {
          return openLongChat(parsed);
        }
        return root.LEM.invoke('open_external', [parsed.href]);
      }
      // t.me links stay inside Telegram; the Mini App is not closed (Bot API 7.0+).
      if (parsed.hostname === 't.me' && app?.openTelegramLink) app.openTelegramLink(parsed.href);
      else if (app?.openLink) app.openLink(parsed.href);
      else root.open(parsed.href, '_blank', 'noopener');
      return true;
    },
    confirm(message) {
      const app = tg();
      if (kind() === 'web' && app?.showConfirm) {
        return new Promise((resolve) => {
          try { app.showConfirm(message, (ok) => resolve(Boolean(ok))); } catch (_) { resolve(root.confirm(message)); }
        });
      }
      return Promise.resolve(root.confirm(message));
    },
    haptic(type = 'light') {
      try {
        const feedback = tg()?.HapticFeedback;
        if (!feedback) return;
        if (['success', 'warning', 'error'].includes(type)) feedback.notificationOccurred(type);
        else feedback.impactOccurred(type);
      } catch (_) { /* old clients */ }
    },
    // Mirror the in-app history into Telegram's native back button.
    syncBack(hasHistory, onBack) {
      const button = tg()?.BackButton;
      if (!button || kind() !== 'web') return false;
      if (!this._backBound) { button.onClick(() => this._onBack && this._onBack()); this._backBound = true; }
      this._onBack = onBack;
      if (hasHistory) button.show(); else button.hide();
      return true;
    },
    applyTheme() {
      const app = tg();
      if (!app || kind() !== 'web') return;
      const doc = root.document.documentElement;
      doc.dataset.shell = 'telegram';
      // Follow Telegram's light/dark scheme and paint its chrome in the app's ground colour.
      const paint = () => {
        const dark = app.colorScheme === 'dark';
        doc.dataset.scheme = dark ? 'dark' : 'light';
        const ground = dark ? '#0B1220' : '#F8FAFC';
        try { app.setHeaderColor?.(ground); app.setBackgroundColor?.(ground); app.setBottomBarColor?.(dark ? '#151E2E' : '#FFFFFF'); } catch (_) { /* optional */ }
      };
      paint();
      try { app.onEvent?.('themeChanged', paint); } catch (_) { /* optional */ }
      try { app.expand(); } catch (_) { /* optional */ }
      try { app.disableVerticalSwipes?.(); } catch (_) { /* optional */ }
    },
    onVisibility(callback) {
      const app = tg();
      if (app?.onEvent) {
        app.onEvent('activated', () => callback(true));
        app.onEvent('deactivated', () => callback(false));
      }
    },
  };
});
