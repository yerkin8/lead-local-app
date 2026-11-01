// Desktop shell after the Mini App merge (docs/unify): static guarantees the UI tour does not re-check every time.
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const UI = path.join(__dirname, '..', 'app', 'desktop_ui');
const read = (name) => fs.readFileSync(path.join(UI, name), 'utf8');
const html = read('index.html');
const scripts = [...html.matchAll(/<script src="([^"]+)"/g)].map((m) => m[1]);

test('transport and platform load first, «Рассылка» last', () => {
  assert.deepStrictEqual(scripts.slice(0, 2), ['transport.js', 'platform.js']);
  assert.strictEqual(scripts.at(-1), 'mailing.js');
  for (const name of ['ai.js', 'stats.js', 'today.js', 'leads.js', 'desk-shell.js']) assert.ok(scripts.includes(name), name);
  assert.ok(scripts.indexOf('stats.js') < scripts.indexOf('leads.js'), 'leads.js embeds the stats page in «Итоги»');
});

test('Desktop keeps its light design: no Mini App web/mobile/dark styles', () => {
  for (const css of ['web.css', 'mobile.css', 'dark.css', 'polish.css']) assert.ok(!html.includes(css), css);
  for (const css of ['lem.css', 'style.css', 'leads.css', 'ai.css', 'mailing.css']) assert.ok(html.includes(css), css);
});

test('every section is in the sidebar', () => {
  const views = [...html.matchAll(/data-view="([a-z]+)"/g)].map((m) => m[1]);
  assert.deepStrictEqual(views, ['leads', 'results', 'today', 'search', 'scripts', 'ai', 'mailing', 'media', 'settings']);
});

test('start page is «Лиды» and the Desktop search form is not replaced', () => {
  assert.match(read('app.js'), /const state=\{view:'leads',/);
  for (const file of ['leads.js', 'today.js', 'ai.js', 'stats.js', 'desk-shell.js']) {
    assert.doesNotMatch(read(file), /^\s*state\.view\s*=/m, file);
  }
  assert.doesNotMatch(read('leads.js'), /search:\s*\(\)\s*=>\s*leadSearchPage/);
});

test('no direct pywebview access and no independent intervals in the new modules', () => {
  for (const file of ['leads.js', 'today.js', 'ai.js', 'stats.js', 'stage9.js', 'app.js', 'flows.js', 'desk-shell.js']) {
    const text = read(file);
    assert.doesNotMatch(text, /pywebview/, file);
    assert.doesNotMatch(text, /setInterval\(/, file);
  }
});

test('«База» and «Панель» are gone; the card belongs to «Лиды»', () => {
  assert.ok(!html.includes('glass-folder'), 'glass folders removed');
  for (const file of ['app.js', 'flows.js', 'stage9.js', 'leads.js', 'today.js']) {
    assert.doesNotMatch(read(file), /baseLevel|glassFolderCards|data-nav="(dashboard|businesses)"|\['(dashboard|businesses)', '/, file);
  }
  assert.match(read('desk-shell.js'), /lead: 'leads', telegram: 'leads'/);
  for (const file of ['app.js', 'stage9.js', 'flows.js', 'leads.js']) assert.doesNotMatch(read(file), /setView\('detail'|state\.view==='detail'/);
});

test('form rows keep one control height and a common top (redesign 06 layout guard)', () => {
  const css = read('style.css');
  const guard = css.slice(css.indexOf('/* === Layout guard (redesign 06)'));
  assert.ok(guard.length > 100, 'layout guard block must exist and stay last');
  assert.match(guard, /\.form-grid \{ align-items: start; \}/);
  assert.match(guard, /label\.field \{ align-content: start;/);
  assert.match(guard, /label\.field select \{\s*height: 42px;\s*min-height: 42px;\s*max-height: 42px;/);
  for (const name of ['lem.css', 'mailing.css', 'ai.css', 'leads.css']) {
    assert.doesNotMatch(read(name), /\.form-grid[^{]*\{[^}]*align-items:\s*stretch/, name);
  }
  assert.doesNotMatch(read('flows.js'), /'Города \(через запятую/);
});

test('«Настройки»: theme before first paint, keys, two imports, no «Обзор» or old data notes', () => {
  const head = html.slice(0, html.indexOf('</head>'));
  assert.match(head, /dataset\.theme=/, 'theme is set in <head> before CSS paints');
  assert.ok(scripts.indexOf('settings.js') > scripts.indexOf('today.js') && scripts.indexOf('settings.js') > scripts.indexOf('ai.js'));
  assert.match(read('lem.css'), /:root\[data-theme="dark"\]/);
  const s = read('settings.js');
  for (const fragment of ['data-theme-set', 'mailing_clear_keys', 'mailing_save_keys', 'data-action="import"', 'data-import-sqlite', 'ui_settings_save']) {
    assert.ok(s.includes(fragment), fragment);
  }
  assert.doesNotMatch(read('today.js'), /Открыть обзор/);
  for (const gone of ['data-replace-database>', 'data-open-logs>', 'debug-html', 'migration_notice']) {
    assert.ok(!read('flows.js').includes(gone) && !s.includes(gone), gone);
  }
});

// docs/unify/redesign/09-review-fixes.md
const nav = require(path.join(UI, 'lead-nav.js'));
test('lead card prev/next follow the list order and stop at the edges', () => {
  const ids = ['a', 'b', 'c'];
  assert.deepStrictEqual(nav.neighbours(ids, 'a'), { prev: null, next: 'b', index: 0 });
  assert.deepStrictEqual(nav.neighbours(ids, 'b'), { prev: 'a', next: 'c', index: 1 });
  assert.deepStrictEqual(nav.neighbours(ids, 'c'), { prev: 'b', next: null, index: 2 });
  assert.deepStrictEqual(nav.neighbours(ids, 'x'), { prev: null, next: null, index: -1 });
  assert.match(read('leads.js'), /data-lead-prev/);
  assert.match(read('leads.js'), /data-lead-next/);
  assert.match(read('leads.js'), /lem\.leads\.autoNext/);
});
test('«Назад» is hidden on sidebar roots and named after the place it returns to', () => {
  for (const view of ['leads', 'results', 'today', 'search', 'scripts', 'ai', 'mailing', 'settings']) assert.strictEqual(nav.backVisible(view, true, false), false, view);
  assert.strictEqual(nav.backVisible('media', true, false), false);
  assert.strictEqual(nav.backVisible('media', true, true), true);
  assert.strictEqual(nav.backVisible('lead', true, false), true);
  assert.strictEqual(nav.backVisible('lead', false, false), false);
  assert.strictEqual(nav.backLabel('leads'), '← К лидам');
  assert.strictEqual(nav.backLabel('detail'), '← Назад');
});
test('site types in words, local time without «T», no dangling separator', () => {
  assert.strictEqual(nav.siteType('own_site'), 'Свой сайт');
  assert.strictEqual(nav.siteType('weak_site'), 'Конструктор');
  assert.strictEqual(nav.siteType('???'), '');
  assert.match(nav.localTime('2026-10-31T09:04:00'), /^31\.10, 09:04$/);
  assert.doesNotMatch(nav.localTime('2026-10-31T10:07:00+00:00'), /T/);
  assert.doesNotMatch(read('leads.js'), /fmtRating\(b\)\)\} · \$\{E\(TYPE/);
  assert.doesNotMatch(read('flows.js'), /'<label class="field">Отзывы на организацию[^']*\$\{/);
});
test('flag storage survives a missing localStorage', () => {
  assert.strictEqual(nav.readFlag('x', true), true);
  nav.writeFlag('x', false);
});
