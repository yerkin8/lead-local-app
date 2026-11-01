// «ИИ-чат» pure logic: composer keys, autosize, typewriter loop, Markdown cleanup, markup rules.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const core = require('../app/desktop_ui/ai-core.js');

const UI = path.join(__dirname, '..', 'app', 'desktop_ui');

test('Enter sends, Shift+Enter and IME composition do not', () => {
  assert.equal(core.composerKey({ key: 'Enter' }), 'send');
  assert.equal(core.composerKey({ key: 'Enter', shiftKey: true }), null);
  assert.equal(core.composerKey({ key: 'Enter', isComposing: true }), null);
  assert.equal(core.composerKey({ key: 'a' }), null);
});

test('autosize clamps the textarea height', () => {
  assert.equal(core.autosize(10), 44);
  assert.equal(core.autosize(120), 120);
  assert.equal(core.autosize(900), 200);
});

test('typewriter types, holds, erases, pauses, moves to the next prompt and loops', () => {
  const prompts = ['ab', 'c'];
  let s = { i: 0, len: 0, phase: 'type' };
  const seen = [];
  const delays = [];
  for (let n = 0; n < 16; n += 1) {
    const { next, delay } = core.typeStep(s, prompts);
    s = next; delays.push(delay); seen.push(`${s.phase}:${core.typeText(s, prompts)}`);
  }
  assert.deepEqual(seen.slice(0, 7), ['type:a', 'type:ab', 'hold:ab', 'erase:ab', 'erase:a', 'erase:', 'gap:']);
  assert.equal(seen[7], 'type:'); // next prompt starts empty
  assert.equal(s.i < prompts.length, true);
  assert.ok(delays.includes(core.TYPE.holdMs) && delays.includes(core.TYPE.gapMs));
  assert.ok(seen.includes('type:c'));
});

test('plain() strips Markdown the messenger would show', () => {
  assert.equal(core.plain('## Привет\n**жирный** и __тоже__\n> цитата'), 'Привет\nжирный и тоже\nцитата');
});

test('greeting by hour', () => {
  assert.equal(core.greeting(8), 'Доброе утро');
  assert.equal(core.greeting(14), 'Добрый день');
  assert.equal(core.greeting(21), 'Добрый вечер');
  assert.equal(core.greeting(2), 'Доброй ночи');
});

test('chat markup: no «Опишите» plate, core loads before ai.js, dark overrides and reduced motion exist', () => {
  const js = fs.readFileSync(path.join(UI, 'ai.js'), 'utf8');
  const css = fs.readFileSync(path.join(UI, 'ai.css'), 'utf8');
  const html = fs.readFileSync(path.join(UI, 'index.html'), 'utf8');
  assert.ok(!/Опишите, какое сообщение нужно/.test(js));
  assert.ok(html.indexOf('ai-core.js') < html.indexOf('src="ai.js"'));
  assert.ok(css.includes(':root[data-theme="dark"]'));
  assert.ok(css.includes('prefers-reduced-motion'));
  assert.ok(js.includes('async function settingsKeyBlock'));
});
