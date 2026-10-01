// Run: node --test tests/test_deferred_auth_modal.cjs (no network or real timers).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const html = fs.readFileSync(path.join(__dirname, '../frontend/index.html'), 'utf8');
const apiSource = fs.readFileSync(path.join(__dirname, '../frontend/js/api.js'), 'utf8');
const modalScripts = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)]
  .map(match => match[1]).filter(source => source.includes('AlarmApi.configure'));
const response = (status, data) => ({status, ok: status === 200, json: async () => data});
const flush = () => new Promise(resolve => setImmediate(resolve));

function modal() {
  assert.equal(modalScripts.length, 1, 'load the actual modal script from index.html');
  const tag = html.match(/<div\b[^>]*\bid="loginModal"[^>]*>/);
  assert.ok(tag);
  assert.match(tag[0], /\shidden(?:\s|>)/);
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) elements.set(id, {
      style: {}, value: '', disabled: false, listeners: {}, focusCount: 0,
      addEventListener(event, listener) { this.listeners[event] = listener; },
      focus() { this.focusCount++; },
      submit() { throw new Error('Unexpected native form submission'); },
    });
    return elements.get(id);
  }
  let hidden = true;
  const hiddenWrites = [];
  Object.defineProperty(element('loginModal'), 'hidden', {
    get: () => hidden,
    set(value) { hiddenWrites.push(value); hidden = value; },
  });
  const configurations = [];
  const timers = new Map();
  let timerId = 0;
  let loginResponse = response(200, {ok: true});
  const location = {href: 'http://localhost/'};
  const context = {
    AlarmApi: {configure(options) { configurations.push(options); }},
    document: {getElementById: element},
    window: {location},
    localStorage: {getItem() { return null; }, setItem() {}},
    FormData: class {},
    fetch: async url => {
      if (url === '/api/departments/public') return response(200, []);
      assert.equal(url, '/login', 'unexpected fetch must not reach the network');
      return loginResponse;
    },
    setInterval(callback) { timers.set(++timerId, callback); return timerId; },
    clearInterval(id) { timers.delete(id); },
  };
  vm.runInNewContext(modalScripts[0], context);
  assert.equal(configurations.length, 1);
  assert.equal(typeof configurations[0].on401, 'function');
  return {
    element, hiddenWrites, location, on401: configurations[0].on401,
    setLoginResponse(value) { loginResponse = value; },
    tick() { for (const callback of [...timers.values()]) callback(); },
    submit() {
      return element('loginForm').listeners.submit({
        preventDefault() {}, target: element('loginForm'),
      });
    },
  };
}

test('configure installs a 401 handler and retries the original API request', async () => {
  const calls = [];
  const expected = response(200, {result: 'ok'});
  const context = {
    window: {}, location: {pathname: '/', search: ''},
    fetch: async (...args) => {
      calls.push(args);
      return calls.length === 1 ? response(401, {}) : expected;
    },
  };
  vm.runInNewContext(apiSource, context);
  let intercepted = 0;
  context.window.AlarmApi.configure({on401: retry => {
    intercepted++;
    assert.equal(typeof retry, 'function');
    return retry();
  }});
  const result = await context.window.AlarmApi.post('/api/feedback', {result: 'ok'});
  assert.equal(intercepted, 1);
  assert.equal(result, expected);
  assert.equal(calls.length, 2);
  assert.equal(calls[1][0], '/api/feedback');
  assert.equal(calls[1][1], calls[0][1], 'reuse original request options');
  assert.equal(calls[1][1].method, 'POST');
  assert.equal(calls[1][1].body, JSON.stringify({result: 'ok'}));
  assert.equal(context.location.href, undefined);
});

test('page registers on401 and opens/focuses the modal only when hidden', async () => {
  const m = modal();
  assert.equal(m.element('loginModal').hidden, true);
  assert.equal(m.element('pw').focusCount, 0);
  const first = m.on401(async () => 'first');
  assert.equal(m.element('loginModal').hidden, false);
  const second = m.on401(async () => 'second');
  assert.deepEqual(m.hiddenWrites, [false]);
  assert.equal(m.element('pw').focusCount, 1);
  await m.submit();
  assert.deepEqual(await Promise.all([first, second]), ['first', 'second']);
});

test('all queued requests run once after login and resolve to their own responses', async () => {
  const m = modal();
  const calls = [];
  const pending = [1, 2, 3].map(id => m.on401(async () => {
    calls.push(id);
    return id;
  }));
  assert.deepEqual(calls, []);
  await m.submit();
  assert.deepEqual(calls, [1, 2, 3]);
  assert.deepEqual(await Promise.all(pending), [1, 2, 3]);
  assert.equal(m.element('loginModal').hidden, true);
  assert.equal(m.location.href, 'http://localhost/');
  await m.submit();
  assert.deepEqual(calls, [1, 2, 3], 'successful login consumes the entire old queue');
});

test('queue is cleared BEFORE retries: a reentrant 401 survives for the next login', async () => {
  const m = modal();
  const calls = [];
  let completed = false;
  // Enqueue synchronously during replay to exercise the ordering boundary.
  // Clearing after forEach would discard this request and leave first pending.
  const first = m.on401(() => {
    calls.push('first attempt');
    return m.on401(async () => {
      calls.push('second attempt');
      return 'recovered';
    });
  });
  first.then(() => { completed = true; });
  const other = m.on401(async () => { calls.push('other'); return 'other result'; });
  await m.submit();
  await flush();
  assert.deepEqual(calls, ['first attempt', 'other']);
  assert.equal(completed, false);
  assert.equal(await other, 'other result');
  assert.equal(m.element('loginModal').hidden, false);
  await m.submit();
  await flush();
  assert.deepEqual(calls, ['first attempt', 'other', 'second attempt']);
  assert.equal(completed, true, 'new 401 must not be discarded by old queue cleanup');
  assert.equal(await first, 'recovered');
  assert.equal(m.element('loginModal').hidden, true);
  await m.submit();
  assert.equal(calls.length, 3, 'neither old nor new requests are replayed twice');
});

for (const status of [401, 429]) {
  test(`login failure ${status} preserves the open modal and every pending request`, async () => {
    const m = modal();
    const calls = [];
    let settled = 0;
    const pending = [1, 2].map(id => m.on401(async () => {
      calls.push(id);
      return id;
    }).then(value => { settled++; return value; }));
    m.setLoginResponse(response(status, {ok: false, throttled: 2}));
    await m.submit();
    await flush();
    assert.deepEqual(calls, []);
    assert.equal(settled, 0);
    assert.equal(m.element('loginModal').hidden, false);
    assert.deepEqual(m.hiddenWrites, [false]);
    if (status === 429) {
      assert.equal(m.element('submitBtn').disabled, true);
      assert.equal(m.element('pw').disabled, true);
      m.tick();
      m.tick();
      assert.equal(m.element('pw').disabled, false);
      assert.deepEqual(calls, [], 'countdown completion must not replay requests');
    } else {
      assert.equal(m.element('errMsg').style.display, 'block');
    }
    assert.equal(m.element('submitBtn').disabled, false);
    m.setLoginResponse(response(200, {ok: true}));
    await m.submit();
    assert.deepEqual(calls, [1, 2], 'failed login must not drop the pending queue');
    assert.deepEqual(await Promise.all(pending), [1, 2]);
  });
}

test('a retry rejection reaches its caller without blocking other queued requests', async () => {
  const m = modal();
  const error = new Error('offline');
  const failed = m.on401(async () => { throw error; });
  const rejection = assert.rejects(failed, err => err === error);
  const successful = m.on401(async () => 'ok');
  await m.submit();
  await rejection;
  assert.equal(await successful, 'ok');
});
