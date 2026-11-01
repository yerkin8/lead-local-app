const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const UI = path.join(__dirname, '..', 'app', 'desktop_ui');
const { createTransport, detectKind, AuthError } = require(path.join(UI, 'transport.js'));

function response(status, body) {
  return { status, ok: status >= 200 && status < 300, json: async () => body };
}

test('kind follows page protocol', () => {
  assert.equal(detectKind({ protocol: 'file:' }), 'desktop');
  assert.equal(detectKind({ protocol: 'https:' }), 'web');
  assert.equal(detectKind({ protocol: 'http:' }), 'web');
  assert.equal(detectKind(undefined), 'desktop');
});

test('web sends RPC with initData and returns the bridge-shaped answer', async () => {
  const calls = [];
  const t = createTransport({ kind: 'web', initData: 'signed', devAuth: false,
    fetch: async (url, init) => { calls.push([url, init]); return response(200, { ok: true, data: 7 }); } });
  assert.deepEqual(await t.invoke('business', ['42']), { ok: true, data: 7 });
  assert.equal(calls[0][0], '/api/rpc/business');
  assert.equal(calls[0][1].headers['X-Telegram-Init-Data'], 'signed');
  assert.equal(calls[0][1].headers['X-Dev-Auth'], undefined);
  assert.deepEqual(JSON.parse(calls[0][1].body), { args: ['42'] });
});

test('401 and 403 raise AuthError, others become readable errors', async () => {
  for (const status of [401, 403]) {
    const t = createTransport({ kind: 'web', initData: '', devAuth: false, fetch: async () => response(status, {}) });
    await assert.rejects(t.invoke('stats'), (e) => e instanceof AuthError && e.status === status);
  }
  const limited = createTransport({ kind: 'web', initData: 'x', devAuth: false, fetch: async () => response(429, {}) });
  assert.match((await limited.invoke('stats')).error, /Слишком много/);
  const offline = createTransport({ kind: 'web', initData: 'x', devAuth: false, fetch: async () => { throw Error('net'); } });
  assert.match((await offline.invoke('stats')).error, /Нет связи/);
  const server = createTransport({ kind: 'web', initData: 'x', devAuth: false, fetch: async () => response(422, { detail: 'Аргументы должны быть списком' }) });
  assert.equal((await server.invoke('stats')).error, 'Аргументы должны быть списком');
});

test('desktop-only methods never reach the server', async () => {
  let fetched = false;
  const t = createTransport({ kind: 'web', initData: 'x', devAuth: false, fetch: async () => { fetched = true; } });
  for (const method of ['media_open', 'media_save', 'import_sqlite', 'open_logs', 'business_refresh']) {
    assert.equal((await t.invoke(method, [])).ok, false);
    assert.ok(t.unavailable(method));
  }
  assert.equal(fetched, false);
});

test('desktop calls the pywebview bridge', async () => {
  global.pywebview = { api: { stats: async (...args) => ({ ok: true, data: args }) } };
  const t = createTransport({ kind: 'desktop' });
  assert.deepEqual(await t.invoke('stats', [1, 2]), { ok: true, data: [1, 2] });
  await assert.rejects(t.invoke('missing', []), /Мост/);
  delete global.pywebview;
});

test('platform: links, features and back button in Telegram', async () => {
  const opened = [];
  let backShown = null;
  let backHandler = null;
  global.LEM = { kind: 'web' };
  global.Telegram = { WebApp: {
    initData: 'x',
    openTelegramLink: (u) => opened.push(['tg', u]),
    openLink: (u) => opened.push(['web', u]),
    BackButton: { onClick: (fn) => { backHandler = fn; }, show: () => { backShown = true; }, hide: () => { backShown = false; } },
  } };
  delete require.cache[require.resolve(path.join(UI, 'platform.js'))];
  const platform = require(path.join(UI, 'platform.js'));
  platform.openLink('https://t.me/+79001234567?text=hi');
  platform.openLink('https://yandex.ru/maps/org/x/1/');
  assert.deepEqual(opened, [['tg', 'https://t.me/+79001234567?text=hi'], ['web', 'https://yandex.ru/maps/org/x/1/']]);
  assert.throws(() => platform.openLink('javascript:alert(1)'), /веб-ссылку/);
  assert.equal(platform.can('export'), false);
  assert.equal(platform.can('search'), true);
  let wentBack = false;
  assert.equal(platform.syncBack(true, () => { wentBack = true; }), true);
  assert.equal(backShown, true);
  backHandler();
  assert.equal(wentBack, true);
  platform.syncBack(false, () => {});
  assert.equal(backShown, false);
  global.LEM.kind = 'desktop';
  assert.equal(platform.can('export'), true);
  delete global.LEM; delete global.Telegram;
});

test('desktop: a chat link too long for Windows opens without text and the text is copied (K4)', async () => {
  const invoked = [];
  const toasts = [];
  let clipboard = null;
  global.LEM = { kind: 'desktop', invoke: async (name, args) => { invoked.push([name, ...args]); return true; } };
  global.toast = (message, error) => toasts.push([message, Boolean(error)]);
  const savedNavigator = Object.getOwnPropertyDescriptor(globalThis, 'navigator');
  Object.defineProperty(globalThis, 'navigator', { configurable: true, value: { clipboard: { writeText: async (t) => { clipboard = t; } } } });
  delete require.cache[require.resolve(path.join(UI, 'platform.js'))];
  const platform = require(path.join(UI, 'platform.js'));
  const longText = 'Здравствуйте! '.repeat(60);
  await platform.openLink('https://t.me/+79001234567?text=' + encodeURIComponent(longText));
  assert.equal(clipboard, longText);
  assert.deepEqual(invoked, [['open_external', 'https://t.me/+79001234567']]);
  assert.match(toasts[0][0], /Ctrl\+V/);
  await platform.openLink('https://t.me/+79001234567?text=' + encodeURIComponent('Короткий текст'));
  assert.equal(invoked[1][1], 'https://t.me/+79001234567?text=' + encodeURIComponent('Короткий текст'));
  if (savedNavigator) Object.defineProperty(globalThis, 'navigator', savedNavigator); else delete globalThis.navigator;
  delete global.LEM; delete global.toast;
});

test('export and backup go to the chat with the bot in the Mini App', async () => {
  const urls = [];
  const t = createTransport({ kind: 'web', initData: 'x', devAuth: false,
    fetch: async (url, init) => { urls.push([url, JSON.parse(init.body).args]); return response(200, { ok: true, data: 1 }); } });
  await t.invoke('export_xlsx', []);
  await t.invoke('export_scope', [{ run_id: 'r1' }]);
  await t.invoke('backup', []);
  assert.deepEqual(urls, [['/api/rpc/export_send', [null]], ['/api/rpc/export_send', [{ run_id: 'r1' }]], ['/api/rpc/backup_send', []]]);
});
