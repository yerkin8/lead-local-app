// Pure helpers for the lead card and lists (docs/unify/redesign/09-review-fixes.md). Also loaded by node tests.
(function (root) {
  const SITE_TYPES = { no_site: 'Без сайта', weak_site: 'Конструктор', unavailable: 'Сайт не работает', own_site: 'Свой сайт', other: 'Свой сайт' };
  const ROOT_VIEWS = ['leads', 'results', 'today', 'search', 'scripts', 'ai', 'mailing', 'media', 'settings'];
  const BACK_LABELS = { leads: '← К лидам', today: '← К «Сегодня»', results: '← К итогам', search: '← К поиску' };

  // Neighbours of `id` in the current list order; null when it is not in the list or at an edge.
  function neighbours(ids, id) {
    const at = ids.indexOf(id);
    if (at < 0) return { prev: null, next: null, index: -1 };
    return { prev: at > 0 ? ids[at - 1] : null, next: at < ids.length - 1 ? ids[at + 1] : null, index: at };
  }
  const siteType = (key) => SITE_TYPES[key] || '';
  // ISO (UTC or local) → «31.10, 09:04» in local time; text that is not a date is returned unchanged.
  function localTime(iso) {
    if (!iso) return '';
    const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : String(iso).replace(' ', 'T'));
    if (Number.isNaN(d.getTime())) return String(iso);
    const p = (n) => String(n).padStart(2, '0');
    return `${p(d.getDate())}.${p(d.getMonth() + 1)}, ${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  // «Назад» is shown only off the sidebar roots (Media counts as nested when opened from a card).
  function backVisible(view, hasHistory, mediaFromCard) {
    if (!hasHistory) return false;
    if (view === 'media') return Boolean(mediaFromCard);
    return !ROOT_VIEWS.includes(view);
  }
  const backLabel = (previousView) => BACK_LABELS[previousView] || '← Назад';
  function readFlag(key, fallback) {
    try { const v = root.localStorage && root.localStorage.getItem(key); return v === null || v === undefined ? fallback : v === '1'; } catch (_) { return fallback; }
  }
  function writeFlag(key, on) { try { root.localStorage.setItem(key, on ? '1' : '0'); } catch (_) { /* storage off */ } }

  const api = { SITE_TYPES, ROOT_VIEWS, neighbours, siteType, localTime, backVisible, backLabel, readFlag, writeFlag };
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.LeadNav = api;
})(typeof window !== 'undefined' ? window : globalThis);
