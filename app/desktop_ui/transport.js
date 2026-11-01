// One `invoke(method, args)` for both shells: pywebview bridge on the desktop (file://),
// HTTP RPC with Telegram initData in the Mini App (https://). Both answer {ok, data | error}.
(function (root, factory) {
  const api = factory(root);
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LEM = api;
})(typeof window !== 'undefined' ? window : globalThis, function (root) {
  const TELEGRAM_SDK = 'https://telegram.org/js/telegram-web-app.js';
  // Methods the Mini App runs locally instead of on the server.
  const WEB_LOCAL = {
    open_external: (url) => root.LEM.platform.openLink(url),
    import_xlsx: () => uploadXlsx(),
  };
  // Desktop methods with a web counterpart under another name: files go to the chat with the bot.
  const WEB_RENAMED = {
    export_xlsx: () => ['export_send', [null]],
    export_scope: (scope) => ['export_send', [scope]],
    backup: () => ['backup_send', []],
  };
  // Desktop-only methods: hidden in the web UI, and refused here as a safety net.
  const WEB_UNAVAILABLE = new Set(['open_logs', 'media_open', 'media_save', 'import_sqlite', 'replace_database', 'complete_setup',
    'business_refresh', 'media_discover_sections', 'lead_add', 'phones_refill', 'cards_job', 'cards_stop']);
  const AUTH_MESSAGES = {
    401: 'Откройте приложение через Telegram-бота',
    403: 'У этого Telegram-аккаунта нет доступа',
    404: 'Эта функция пока недоступна в Mini App',
    429: 'Слишком много запросов, подождите минуту',
  };

  let uploadXlsx = async () => null;

  function detectKind(loc) {
    return /^https?:$/.test((loc && loc.protocol) || '') ? 'web' : 'desktop';
  }

  class AuthError extends Error {
    constructor(message, status) { super(message); this.status = status; }
  }

  function createTransport(options = {}) {
    const kind = options.kind || detectKind(root.location);
    const fetchImpl = options.fetch || ((...a) => root.fetch(...a));
    const initData = () => options.initData ?? root.Telegram?.WebApp?.initData ?? '';
    const devAuth = () => options.devAuth ?? (root.location?.hostname === '127.0.0.1' && !initData());

    async function viaBridge(method, args) {
      const api = root.pywebview?.api;
      if (!api || typeof api[method] !== 'function') throw Error('Мост приложения ещё загружается');
      return api[method](...args);
    }

    async function uploadXlsxImpl() {
      const file = await new Promise((resolve) => {
        const input = root.document.createElement('input');
        input.type = 'file';
        input.accept = '.xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
        input.onchange = () => resolve(input.files && input.files[0] ? input.files[0] : null);
        input.addEventListener('cancel', () => resolve(null));
        input.click();
      });
      if (!file) return null;
      const headers = { 'Content-Type': 'application/octet-stream', 'X-Telegram-Init-Data': initData() };
      if (devAuth()) headers['X-Dev-Auth'] = '1';
      const response = await fetchImpl('/api/upload/xlsx', { method: 'POST', headers, body: file });
      if (response.status === 401 || response.status === 403) throw new AuthError(AUTH_MESSAGES[response.status], response.status);
      const result = await response.json();
      if (!result.ok) throw Error(result.error || 'Не удалось импортировать файл');
      return result.data;
    }
    uploadXlsx = uploadXlsxImpl;

    async function viaHttp(method, args) {
      if (WEB_RENAMED[method]) [method, args] = WEB_RENAMED[method](...args);
      if (WEB_LOCAL[method]) return { ok: true, data: await WEB_LOCAL[method](...args) };
      if (WEB_UNAVAILABLE.has(method)) return { ok: false, error: AUTH_MESSAGES[404] };
      const headers = { 'Content-Type': 'application/json', 'X-Telegram-Init-Data': initData() };
      if (devAuth()) headers['X-Dev-Auth'] = '1';
      let response;
      try {
        response = await fetchImpl(`/api/rpc/${encodeURIComponent(method)}`, {
          method: 'POST', headers, body: JSON.stringify({ args }),
        });
      } catch (error) {
        return { ok: false, error: 'Нет связи с сервером. Проверьте интернет' };
      }
      if (response.status === 401 || response.status === 403) {
        throw new AuthError(AUTH_MESSAGES[response.status], response.status);
      }
      if (!response.ok) {
        let detail = '';
        try { detail = (await response.json()).detail; } catch (_) { /* not JSON */ }
        return { ok: false, error: AUTH_MESSAGES[response.status] || (typeof detail === 'string' && detail) || `Ошибка сервера (${response.status})` };
      }
      return response.json();
    }

    return {
      kind,
      AuthError,
      unavailable: (method) => kind === 'web' && WEB_UNAVAILABLE.has(method),
      invoke: (method, args = []) => (kind === 'web' ? viaHttp(method, args) : viaBridge(method, args)),
    };
  }

  function loadScript(src) {
    return new Promise((resolve) => {
      const node = root.document.createElement('script');
      node.src = src;
      node.onload = node.onerror = () => resolve();
      root.document.head.appendChild(node);
    });
  }

  const api = {
    createTransport, detectKind, AuthError, WEB_UNAVAILABLE,
    transport: null,
    get kind() { return api.transport ? api.transport.kind : detectKind(root.location); },
    invoke: (method, args) => api.transport.invoke(method, args),
    // Report UI errors to the log without ever throwing.
    report(message, source = '', line = 0, column = 0) {
      try { api.invoke('frontend_error', [String(message).slice(0, 2000), String(source).slice(0, 500), line, column]).catch(() => {}); }
      catch (_) { /* transport not ready */ }
    },
    // Runs `callback` once the backend can be called.
    onReady(callback) {
      if (api.kind === 'desktop') {
        if (root.pywebview?.api) callback();
        else root.addEventListener('pywebviewready', () => callback(), { once: true });
        return;
      }
      api._sdk = api._sdk || (root.Telegram?.WebApp ? Promise.resolve() : loadScript(TELEGRAM_SDK))
        .then(() => root.Telegram?.WebApp?.ready?.());
      api._sdk.then(() => callback());
    },
  };
  if (typeof root.location !== 'undefined') api.transport = createTransport();
  return api;
});
