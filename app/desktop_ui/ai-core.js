// Pure helpers of «ИИ-чат» (no DOM): Markdown cleanup, composer keys, textarea autosize and the typewriter loop.
// Loaded before ai.js as window.LEM_AI_CORE; tests/ai-chat.test.cjs requires it directly.
(function (root) {
  // Models like Markdown; a messenger shows it as stray stars and hashes, so copies and saved scripts drop it.
  const plain = (text) => String(text || '')
    .replace(/\*\*([^*\n][^*]*?)\*\*/g, '$1').replace(/__([^_\n][^_]*?)__/g, '$1')
    .replace(/^#{1,6}[ \t]+/gm, '').replace(/^>[ \t]?/gm, '').trim();

  // Enter sends, Shift+Enter is a new line; Enter during IME composition is not a send.
  const composerKey = (e) => (e.key === 'Enter' && !e.shiftKey && !e.isComposing ? 'send' : null);

  // Height for the composer textarea: grows with content between min and max, then scrolls.
  const autosize = (scrollHeight, min = 44, max = 200) => Math.max(min, Math.min(max, scrollHeight));

  const TYPE = { typeMs: 48, deleteMs: 14, holdMs: 3400, gapMs: 900 };
  // One step of the typewriter: given {i, len, phase} returns the next state and the delay before it.
  // Phases: type → hold → erase → gap → (next prompt) type.
  function typeStep(s, prompts, t = TYPE) {
    const full = prompts[s.i % prompts.length] || '';
    if (s.phase === 'type') {
      if (s.len < full.length) return { next: { ...s, len: s.len + 1 }, delay: t.typeMs };
      return { next: { ...s, phase: 'hold' }, delay: t.holdMs };
    }
    if (s.phase === 'hold') return { next: { ...s, phase: 'erase' }, delay: t.deleteMs };
    if (s.phase === 'erase') {
      if (s.len > 0) return { next: { ...s, len: s.len - 1 }, delay: t.deleteMs };
      return { next: { ...s, phase: 'gap' }, delay: t.gapMs };
    }
    return { next: { i: (s.i + 1) % prompts.length, len: 0, phase: 'type' }, delay: t.typeMs };
  }
  const typeText = (s, prompts) => (prompts[s.i % prompts.length] || '').slice(0, s.len);

  // Russian greeting by local hour.
  const greeting = (hour) => (hour < 5 ? 'Доброй ночи' : hour < 12 ? 'Доброе утро' : hour < 18 ? 'Добрый день' : 'Добрый вечер');

  const api = { plain, composerKey, autosize, typeStep, typeText, greeting, TYPE };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.LEM_AI_CORE = api;
})(typeof window !== 'undefined' ? window : globalThis);
