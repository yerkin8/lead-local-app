const test = require('node:test');
const assert = require('node:assert/strict');
const {createActivityPoller} = require('../app/desktop_ui/activity-poller.js');

class FakeClock {
  constructor() { this.now = 0; this.nextId = 1; this.timers = new Map(); }
  setTimeout = (callback, delay) => {
    const id = this.nextId++;
    this.timers.set(id, {at: this.now + delay, callback});
    return id;
  };
  clearTimeout = id => this.timers.delete(id);
  async advance(milliseconds) {
    const until = this.now + milliseconds;
    while (true) {
      const due = [...this.timers.entries()].filter(([, item]) => item.at <= until)
        .sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      this.timers.delete(due[0]);
      this.now = due[1].at;
      due[1].callback();
      await flush();
    }
    this.now = until;
    await flush();
  }
}

async function flush() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

function snapshot(topic = 'search', items = [], activeCount = 0) {
  return {search: [], media: [], campaigns: [], active_count: activeCount, [topic]: items};
}

test('does not poll when the current view has no activity topics', async () => {
  const clock = new FakeClock();
  let requests = 0, updates = 0;
  const poller = createActivityPoller({
    intervalMs: 6000, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    isPaused: () => false,
    read: async () => { requests++; return snapshot(); },
    onUpdate: async () => { updates++; },
  });
  poller.setScope([]);
  await clock.advance(60000);
  assert.equal(requests, 0);
  assert.equal(updates, 0);
  assert.equal(clock.timers.size, 0);
});

test('checks the selected scope once while idle and stops after finding no active work', async () => {
  const clock = new FakeClock();
  let requests = 0, updates = 0;
  let received;
  const poller = createActivityPoller({
    intervalMs: 6000, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    isPaused: () => false,
    read: async known => { requests++; received = known; return snapshot(); },
    onUpdate: async () => { updates++; },
  });
  poller.setScope(['search']);
  await flush();
  await clock.advance(60000);
  assert.equal(requests, 1);
  assert.deepEqual(received.topics, ['search']);
  assert.equal(updates, 1);
  assert.equal(clock.timers.size, 0);
});

test('active task updates use revisions and stop when all selected work completes', async () => {
  const clock = new FakeClock();
  let requests = 0;
  const updates = [];
  const poller = createActivityPoller({
    intervalMs: 6000, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    isPaused: () => false,
    read: async known => {
      requests++;
      if (requests === 1) return snapshot('search', [{id: 'job-1', status: 'running', revision: 'r1'}], 1);
      assert.equal(known.search['job-1'], 'r1');
      return snapshot('search', [{id: 'job-1', status: 'done', revision: 'r2'}], 0);
    },
    onUpdate: async value => updates.push(value),
  });
  poller.setScope(['search']);
  await flush();
  assert.equal(requests, 1);
  await clock.advance(5999);
  assert.equal(requests, 1);
  await clock.advance(1);
  await clock.advance(60000);
  assert.equal(requests, 2);
  assert.equal(updates.length, 2);
  assert.equal(updates[1].search[0].status, 'done');
  assert.equal(clock.timers.size, 0);
});

test('view scope limits backend queries to visible activity', async () => {
  const clock = new FakeClock();
  const scopes = [];
  const poller = createActivityPoller({
    intervalMs: 6000, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    isPaused: () => false,
    read: async known => { scopes.push([...known.topics]); return snapshot(); },
    onUpdate: async () => {},
  });
  poller.setScope(['search']);
  await flush();
  poller.setScope([]);
  poller.setScope(['media']);
  await flush();
  assert.deepEqual(scopes, [['search'], ['media']]);
  assert.equal(clock.timers.size, 0);
});

test('suspended polling makes no request while typing or the window is inactive', async () => {
  const clock = new FakeClock();
  let requests = 0;
  let paused = false;
  const poller = createActivityPoller({
    intervalMs: 6000, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    isPaused: () => paused,
    read: async () => { requests++; return snapshot('search', [{id: 'job', status: 'running', revision: 'r1'}], 1); },
    onUpdate: async () => {},
  });
  poller.setScope(['search']);
  await flush();
  poller.suspend();
  await clock.advance(60000);
  assert.equal(requests, 1);
  poller.resume();
  await flush();
  assert.equal(requests, 2);
  paused = true;
  poller.suspend();
  await clock.advance(60000);
  assert.equal(requests, 2);
});

test('transient errors back off and recover without an unmanaged interval', async () => {
  const clock = new FakeClock();
  let requests = 0;
  const poller = createActivityPoller({
    intervalMs: 6000, maxBackoffMs: 30000, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    isPaused: () => false,
    read: async () => {
      requests++;
      if (requests === 1) throw new Error('offline');
      return snapshot('search', [{id: 'job', status: 'running', revision: `r${requests}`}], 1);
    },
    onUpdate: async () => {},
  });
  poller.setScope(['search']);
  await flush();
  assert.equal(requests, 1);
  await clock.advance(11999);
  assert.equal(requests, 1);
  await clock.advance(1);
  assert.equal(requests, 2);
  assert.equal(clock.timers.size, 1);
});

test('a result from a previous view is ignored after switching scopes', async () => {
  const clock = new FakeClock();
  let resolveOld;
  const updates = [];
  const scopes = [];
  const poller = createActivityPoller({
    intervalMs: 6000, setTimeout: clock.setTimeout, clearTimeout: clock.clearTimeout,
    isPaused: () => false,
    read: known => {
      scopes.push([...known.topics]);
      if (known.topics[0] === 'search') return new Promise(resolve => { resolveOld = resolve; });
      return Promise.resolve(snapshot('media', [{id: 'media-1', status: 'running', revision: 'm1'}], 1));
    },
    onUpdate: async value => updates.push(value),
  });
  poller.setScope(['search']);
  poller.setScope(['media']);
  await flush();
  resolveOld(snapshot('search', [{id: 'search-1', status: 'running', revision: 's1'}], 1));
  await flush();
  assert.deepEqual(scopes, [['search'], ['media']]);
  assert.equal(updates.length, 1);
  assert.equal(updates[0].media[0].id, 'media-1');
});
