(function (root) {
  const TOPICS = ['search', 'media', 'campaigns'];

  function createActivityPoller(options) {
    const intervalMs = options.intervalMs || 6000;
    const maxBackoffMs = options.maxBackoffMs || 30000;
    const setTimer = options.setTimeout || root.setTimeout.bind(root);
    const clearTimer = options.clearTimeout || root.clearTimeout.bind(root);
    let topics = [];
    let running = false;
    let paused = false;
    let inFlight = false;
    let timer = null;
    let pending = null;
    let generation = 0;
    let failures = 0;
    let activeCount = null;
    let refreshAfterFlight = false;
    let known = emptyKnown();

    function emptyKnown() {
      return {topics: [...topics], search: {}, media: {}, campaigns: {}};
    }

    function normalizeTopics(values) {
      if (!Array.isArray(values)) return [];
      return TOPICS.filter(topic => values.includes(topic));
    }

    function sameTopics(left, right) {
      return left.length === right.length && left.every((value, index) => value === right[index]);
    }

    function clearScheduled() {
      if (timer !== null) clearTimer(timer);
      timer = null;
    }

    function mergeKnown(snapshot) {
      for (const topic of topics) {
        for (const item of snapshot[topic] || []) {
          const id = String(item.id);
          if (['queued', 'running'].includes(item.status)) {
            if (item.revision) known[topic][id] = item.revision;
          } else {
            delete known[topic][id];
          }
        }
      }
    }

    function schedule(delay = intervalMs) {
      clearScheduled();
      if (!running || paused || !topics.length || inFlight || options.isPaused()) return;
      timer = setTimer(() => {
        timer = null;
        poll();
      }, delay);
    }

    async function apply(snapshot, token) {
      if (!running || token !== generation) return;
      const nextCount = Number(snapshot?.active_count || 0);
      if (activeCount !== nextCount || TOPICS.some(topic => (snapshot[topic] || []).length)) {
        await options.onUpdate(snapshot);
      }
      if (!running || token !== generation) return;
      activeCount = nextCount;
      if (nextCount > 0) {
        failures = 0;
        schedule();
      } else {
        running = false;
        clearScheduled();
        known = emptyKnown();
      }
    }

    async function poll() {
      if (!running || paused || !topics.length || options.isPaused()) return;
      if (inFlight) {
        refreshAfterFlight = true;
        return;
      }
      inFlight = true;
      const token = generation;
      try {
        const snapshot = await options.read(known);
        if (token !== generation || !running) return;
        mergeKnown(snapshot);
        if (paused || options.isPaused()) {
          pending = {snapshot, token};
          return;
        }
        failures = 0;
        await apply(snapshot, token);
      } catch (error) {
        if (token === generation && running && !paused && !options.isPaused()) {
          failures += 1;
          const backoff = Math.min(maxBackoffMs, intervalMs * (2 ** Math.min(failures, 4)));
          schedule(backoff);
        }
      } finally {
        inFlight = false;
        if (token !== generation && running && !paused && !options.isPaused()) {
          poll();
        } else if (pending && running && !paused && !options.isPaused()) {
          const next = pending;
          pending = null;
          await apply(next.snapshot, next.token);
        } else if (refreshAfterFlight && running && !paused && !options.isPaused()) {
          refreshAfterFlight = false;
          poll();
        } else if (running && !paused && timer === null && !pending) {
          schedule(failures ? Math.min(maxBackoffMs, intervalMs * (2 ** Math.min(failures, 4))) : intervalMs);
        }
      }
    }

    function setScope(nextTopics) {
      const next = normalizeTopics(nextTopics);
      if (sameTopics(topics, next)) return;
      generation += 1;
      clearScheduled();
      pending = null;
      refreshAfterFlight = false;
      topics = next;
      known = emptyKnown();
      activeCount = null;
      failures = 0;
      running = topics.length > 0;
      if (running && !paused && !options.isPaused() && !inFlight) poll();
    }

    function refresh() {
      if (!topics.length) return;
      running = true;
      clearScheduled();
      failures = 0;
      if (paused || options.isPaused()) return;
      if (inFlight) refreshAfterFlight = true;
      else poll();
    }

    function suspend() {
      paused = true;
      clearScheduled();
    }

    function resume() {
      paused = false;
      if (options.isPaused() || !topics.length) return;
      if (pending && !inFlight) {
        const next = pending;
        pending = null;
        apply(next.snapshot, next.token);
      } else if (!inFlight && running && timer === null) {
        poll();
      }
    }

    function stop() {
      generation += 1;
      running = false;
      topics = [];
      clearScheduled();
      pending = null;
      refreshAfterFlight = false;
      known = emptyKnown();
      activeCount = null;
      failures = 0;
    }

    return {setScope, refresh, suspend, resume, stop};
  }

  root.createActivityPoller = createActivityPoller;
  if (typeof module !== 'undefined' && module.exports) module.exports = {createActivityPoller};
})(globalThis);
