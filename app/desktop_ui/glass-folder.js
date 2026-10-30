(function () {
  'use strict';

  const CACHE_LIMIT = 64;
  const textureCache = new Map();
  const pending = [];
  const pendingCanvases = new WeakSet();
  const visibleCanvases = new Set();
  let frameQueued = false;
  let folderObserver = null;
  let generatedId = 0;

  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[character]));

  function dataAttributes(values) {
    return Object.entries(values || {}).filter(([name]) => /^data-[a-z0-9-]+$/i.test(name))
      .map(([name, value]) => ` ${name}="${escapeHtml(value)}"`).join('');
  }

  function stableSeed(value) {
    let hash = 2166136261;
    for (const character of String(value || 'LeadEqualMoney')) {
      hash ^= character.codePointAt(0);
      hash = Math.imul(hash, 16777619);
    }
    return hash >>> 0;
  }

  function random(seed) {
    let value = seed >>> 0;
    return () => {
      value = (value + 0x6D2B79F5) >>> 0;
      let mixed = value;
      mixed = Math.imul(mixed ^ mixed >>> 15, mixed | 1);
      mixed ^= mixed + Math.imul(mixed ^ mixed >>> 7, mixed | 61);
      return ((mixed ^ mixed >>> 14) >>> 0) / 4294967296;
    };
  }

  function readColor(name, fallback) {
    const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    const match = value.match(/^#([\da-f]{6})$/i);
    if (!match) return fallback;
    const hex = match[1];
    return [parseInt(hex.slice(0, 2), 16), parseInt(hex.slice(2, 4), 16), parseInt(hex.slice(4, 6), 16)];
  }

  const RECIPES = [
    ['--folder-art-blue', '--folder-art-sky', '--folder-art-ice'],
    ['--folder-art-sky', '--folder-art-mint', '--folder-art-ice'],
    ['--folder-art-indigo', '--folder-art-blue', '--folder-art-sky'],
    ['--folder-art-mint', '--folder-art-sky', '--folder-art-ice'],
  ];

  function makeTexture(seed, recipe, sizeKey) {
    const cacheKey = `${recipe}:${seed >>> 0}:${sizeKey}`;
    if (textureCache.has(cacheKey)) {
      const cached = textureCache.get(cacheKey);
      textureCache.delete(cacheKey);
      textureCache.set(cacheKey, cached);
      return cached;
    }

    const width = 104;
    const height = 84;
    const colors = RECIPES[recipe % RECIPES.length].map(name => readColor(name, [220, 232, 248]));
    const noise = random(seed ^ (recipe * 0x9E3779B9));
    const canvas = document.createElement('canvas');
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext('2d', { willReadFrequently: true });
    const image = context.createImageData(width, height);

    for (let y = 0; y < height; y += 1) {
      const ny = y / height;
      for (let x = 0; x < width; x += 1) {
        const nx = x / width;
        const firstWarp = nx + Math.sin(ny * 15 + seed * 0.000011) * 0.075;
        const secondWarp = firstWarp + Math.sin((ny + nx) * 27 + seed * 0.000019) * 0.026;
        const fieldA = Math.sin(secondWarp * 8.4 + Math.sin(ny * 9 + seed * 0.000007) * 1.6);
        const fieldB = Math.cos((ny + Math.sin(nx * 7 + seed * 0.000013) * 0.04) * 7.2 + secondWarp * 2.2);
        const blend = Math.max(0, Math.min(1, 0.5 + fieldA * 0.23 + fieldB * 0.11));
        const grain = (noise() - 0.5) * 9;
        const index = (y * width + x) * 4;
        for (let channel = 0; channel < 3; channel += 1) {
          const left = colors[0][channel] * (1 - blend) + colors[1][channel] * blend;
          const rightBlend = Math.max(0, Math.min(1, 0.25 + fieldB * 0.24));
          const value = left * (1 - rightBlend) + colors[2][channel] * rightBlend + grain;
          image.data[index + channel] = Math.max(0, Math.min(255, value));
        }
        image.data[index + 3] = 255;
      }
    }
    context.putImageData(image, 0, 0);
    textureCache.set(cacheKey, canvas);
    if (textureCache.size > CACHE_LIMIT) textureCache.delete(textureCache.keys().next().value);
    return canvas;
  }

  function paint(record) {
    const { canvas, scene, seed, recipe } = record;
    if (!canvas.isConnected || !scene.isConnected) return;
    const ratio = Math.max(0.8, Math.min(2, window.devicePixelRatio || 1));
    const width = Math.max(1, Math.min(680, Math.round(scene.clientWidth * ratio)));
    const height = Math.max(1, Math.round(width * 0.8));
    canvas.width = width;
    canvas.height = height;
    const sizeKey = `${Math.round(width / 64) * 64}x${Math.round(height / 64) * 64}`;
    const texture = makeTexture(seed, recipe, sizeKey);
    const context = canvas.getContext('2d');
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = 'high';
    context.clearRect(0, 0, width, height);
    context.drawImage(texture, 0, 0, width, height);
  }

  function queuePaint(canvas) {
    if (!canvas || !canvas.isConnected || pendingCanvases.has(canvas)) return;
    const scene = canvas.closest('.glass-folder-scene');
    const card = canvas.closest('.glass-folder-card');
    if (!scene || !card) return;
    pendingCanvases.add(canvas);
    pending.push({ canvas, scene, seed: Number(card.dataset.seed) || 0,
      recipe: Number(card.dataset.recipe) || 0 });
    if (!frameQueued) {
      frameQueued = true;
      requestAnimationFrame(processPaintQueue);
    }
  }

  function processPaintQueue() {
    frameQueued = false;
    const record = pending.shift();
    if (record) {
      pendingCanvases.delete(record.canvas);
      try { paint(record); } catch (_) { /* The solid token background remains as a safe fallback. */ }
    }
    if (pending.length && !frameQueued) {
      frameQueued = true;
      requestAnimationFrame(processPaintQueue);
    }
  }

  function observeCanvases(root) {
    folderObserver?.disconnect();
    visibleCanvases.clear();
    if ('IntersectionObserver' in window) {
      folderObserver = new IntersectionObserver(entries => {
        entries.forEach(entry => {
          const canvas = entry.target.querySelector('.glass-folder-art');
          if (!canvas) return;
          if (entry.isIntersecting) {
            visibleCanvases.add(canvas);
            queuePaint(canvas);
          } else {
            visibleCanvases.delete(canvas);
          }
        });
      }, { rootMargin: '180px 0px', threshold: 0.01 });
      root.querySelectorAll('.glass-folder-scene').forEach(scene => folderObserver.observe(scene));
    } else {
      root.querySelectorAll('.glass-folder-art').forEach(canvas => {
        visibleCanvases.add(canvas);
        queuePaint(canvas);
      });
    }
  }

  function setOpen(card, open, restoreFocus = false) {
    const flap = card?.querySelector('[data-folder-toggle]');
    const files = card?.querySelector('.glass-folder-files');
    if (!card || !flap || !files) return;
    card.toggleAttribute('data-open', open);
    flap.setAttribute('aria-expanded', String(open));
    files.inert = !open;
    files.setAttribute('aria-hidden', String(!open));
    if (!('inert' in files)) {
      files.querySelectorAll('button').forEach(button => { button.tabIndex = open ? 0 : -1; });
    }
    if (restoreFocus) flap.focus();
  }

  function closeOpenFolder(except = null) {
    document.querySelectorAll('.glass-folder-card[data-open]').forEach(card => {
      if (card !== except) setOpen(card, false);
    });
  }

  function renderFiles(files, cardId, title, moreCount) {
    const fileMarkup = (files || []).slice(0, 4).map((file, index) => `<button type="button" class="glass-folder-file" style="--file-index:${index}"${dataAttributes(file.data)}>
      <span class="glass-folder-file-title">${escapeHtml(file.title)}</span><span class="glass-folder-file-meta">${escapeHtml(file.meta || '')}</span></button>`).join('');
    const more = moreCount > 0 ? `<span class="glass-folder-more" aria-label="Ещё ${escapeHtml(moreCount)} элементов">+${escapeHtml(moreCount)}</span>` : '';
    return `<div class="glass-folder-files" id="${cardId}-files" aria-label="Содержимое папки «${escapeHtml(title)}»" aria-hidden="true" inert>
      ${fileMarkup || '<span class="glass-folder-file-empty">Пока нет сохранённых запусков</span>'}${more}</div>`;
  }

  function mount(container, items) {
    if (!container) return;
    closeOpenFolder();
    const cards = (items || []).map(item => {
      const id = `glass-folder-${++generatedId}`;
      const seed = stableSeed(item.key || item.title);
      const files = Array.isArray(item.files) ? item.files : [];
      const recipe = seed % RECIPES.length;
      return `<article class="glass-folder-card" data-folder-card data-seed="${seed}" data-recipe="${recipe}" data-tone="${escapeHtml(item.tone || 'light')}" data-place="${escapeHtml(item.labelPlacement || 'bottom-left')}">
        <div class="glass-folder-scene">
          <canvas class="glass-folder-art" aria-hidden="true"></canvas>
          <div class="glass-folder-back" aria-hidden="true"></div>
          ${renderFiles(files, id, item.title, Number(item.moreCount) || 0)}
          <button type="button" class="glass-folder-flap" data-folder-toggle aria-expanded="false" aria-controls="${id}-files" aria-label="Показать файлы папки «${escapeHtml(item.title)}»">
            <span class="glass-folder-label">${escapeHtml(item.title)}</span>
            <span class="glass-folder-summary">${escapeHtml(item.summary || '')}</span>
            <span class="glass-folder-hint" aria-hidden="true">Нажмите, чтобы открыть файлы</span>
          </button>
          ${item.action === 'none' ? '' : item.action === 'add' ? '<button type="button" class="glass-folder-menu-key glass-folder-menu-key--add" aria-label="Добавить файл">+</button>' : `<button type="button" class="glass-folder-menu-key" data-folder-menu-key aria-haspopup="menu" aria-expanded="false" aria-controls="${id}-menu" aria-label="Действия с папкой «${escapeHtml(item.title)}»">···</button>
          <div class="glass-folder-menu" id="${id}-menu" role="menu" hidden>
            <button type="button" role="menuitem"${dataAttributes(item.openData)}>Открыть</button>
            <button type="button" role="menuitem" data-base-export="${escapeHtml(JSON.stringify(item.exportScope || {}))}">Экспорт</button>
          </div>`}
          <span class="glass-folder-hinge" aria-hidden="true"></span>
        </div>
        ${item.openData ? `<div class="glass-folder-footer"><div><h3>${escapeHtml(item.title)}</h3><p>${escapeHtml(item.summary || '')}</p></div>
          <button type="button" class="btn glass-folder-open" aria-label="Открыть папку «${escapeHtml(item.title)}»"${dataAttributes(item.openData)}>Открыть</button></div>` : ''}
      </article>`;
    }).join('');
    container.innerHTML = cards;
    observeCanvases(container);

    container.querySelectorAll('.glass-folder-scene').forEach(scene => {
      const card = scene.closest('.glass-folder-card');
      setOpen(card, false);
      scene.addEventListener('pointermove', event => {
        if (event.pointerType === 'touch' || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
        const rect = scene.getBoundingClientRect();
        const x = (event.clientX - rect.left) / Math.max(1, rect.width) - 0.5;
        const y = (event.clientY - rect.top) / Math.max(1, rect.height) - 0.5;
        card.style.setProperty('--folder-tilt-x', `${(-y * 5).toFixed(2)}deg`);
        card.style.setProperty('--folder-tilt-y', `${(x * 5).toFixed(2)}deg`);
        card.style.setProperty('--folder-light-x', `${(50 + x * 26).toFixed(1)}%`);
        card.style.setProperty('--folder-light-y', `${(20 + y * 30).toFixed(1)}%`);
      });
      scene.addEventListener('pointerleave', () => {
        card.style.removeProperty('--folder-tilt-x');
        card.style.removeProperty('--folder-tilt-y');
        card.style.removeProperty('--folder-light-x');
        card.style.removeProperty('--folder-light-y');
      });
      scene.addEventListener('focusin', () => card.setAttribute('data-hover', 'true'));
      scene.addEventListener('focusout', event => {
        if (!scene.contains(event.relatedTarget)) card.removeAttribute('data-hover');
      });
      scene.addEventListener('pointerenter', event => {
        if (event.pointerType !== 'touch') card.setAttribute('data-hover', 'true');
      });
      scene.addEventListener('pointerleave', () => card.removeAttribute('data-hover'));
    });
  }

  document.addEventListener('click', event => {
    const menuKey = event.target.closest('[data-folder-menu-key]');
    if (menuKey) {
      event.preventDefault(); event.stopPropagation();
      const menu = menuKey.closest('.glass-folder-card').querySelector('.glass-folder-menu');
      const opening = menu.hidden;
      document.querySelectorAll('.glass-folder-menu').forEach(other => {
        other.hidden = true;
        other.closest('.glass-folder-card').querySelector('[data-folder-menu-key]').setAttribute('aria-expanded', 'false');
      });
      menu.hidden = !opening;
      menuKey.setAttribute('aria-expanded', String(opening));
      if (opening) menu.querySelector('button')?.focus();
      return;
    }
    const toggle = event.target.closest('[data-folder-toggle]');
    if (toggle) {
      event.preventDefault();
      event.stopPropagation();
      const card = toggle.closest('.glass-folder-card');
      const open = !card.hasAttribute('data-open');
      closeOpenFolder(card);
      setOpen(card, open);
      return;
    }
    const openCard = document.querySelector('.glass-folder-card[data-open]');
    if (openCard && !openCard.contains(event.target)) setOpen(openCard, false);
    document.querySelectorAll('.glass-folder-menu:not([hidden])').forEach(menu => {
      if (!menu.closest('.glass-folder-card').contains(event.target) || event.target.closest('[role="menuitem"]')) {
        menu.hidden = true;
        menu.closest('.glass-folder-card').querySelector('[data-folder-menu-key]').setAttribute('aria-expanded', 'false');
      }
    });
  }, true);

  document.addEventListener('keydown', event => {
    if (event.key !== 'Escape') return;
    const menu = document.querySelector('.glass-folder-menu:not([hidden])');
    if (menu) {
      event.preventDefault();
      menu.hidden = true;
      const key = menu.closest('.glass-folder-card').querySelector('[data-folder-menu-key]');
      key.setAttribute('aria-expanded', 'false'); key.focus();
      return;
    }
    const card = document.querySelector('.glass-folder-card[data-open]');
    if (!card) return;
    event.preventDefault();
    setOpen(card, false, true);
  });

  window.addEventListener('resize', () => {
    visibleCanvases.forEach(queuePaint);
  }, { passive: true });

  window.glassFolderCards = { mount, closeAll: () => closeOpenFolder() };
})();
