// Run: node --test tests/test_pending_import_edit_ui.cjs (mocked API; no network).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function dashboard(put) {
  let component;
  const html = fs.readFileSync(path.join(__dirname, '../frontend/dashboard.html'), 'utf8');
  const context = {
    Vue: {createApp(c) {component = c; return {mount() {}};}},
    AlarmApi: {put}, document: {addEventListener() {}},
    console, setTimeout, clearTimeout, navigator: {},
  };
  for (const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
    if (match[1].trim()) vm.runInNewContext(match[1], context);
  }
  const state = component.data();
  for (const [key, method] of Object.entries(component.methods)) state[key] = method.bind(state);
  for (const [key, getter] of Object.entries(component.computed)) {
    Object.defineProperty(state, key, {get: () => getter.call(state)});
  }
  state.whoami = {admin: true, superadmin: true};
  state.viewDept = 'line/2';
  state.showToast = () => {};
  return {state, component, html};
}

const row = () => ({id:1, status:'pending', device_model:'CNC', code:'0099', description:'原文', variant:null, severity:null, cause:'原因', solution:'方案', busy:false});
test('opening prefills a separate draft and cancelling sends no request', () => {
  const calls = [];
  const {state:s, html} = dashboard((...args) => calls.push(args));
  const item = row();
  s.openPendingImport(item);
  assert.equal(s.pendingImportDraft.edits.description, '原文');
  assert.equal(s.pendingImportDraft.edits.variant, '');
  assert.equal(s.pendingImportDraft.edits.cause, '原因');
  assert.equal(s.pendingImportDraft.edits.solution, '方案');
  s.pendingImportDraft.edits.description = '改文';
  s.cancelPendingImport();
  assert.equal(s.pendingImportDraft, null);
  assert.equal(item.description, '原文');
  assert.equal(calls.length, 0);
  for (const field of ['device_model', 'code']) {
    assert.ok(html.includes(`:value="pendingImportDraft.row.${field}" :disabled="true"`));
  }
  assert.ok(html.includes('@click="openPendingImport(row)"'));
  assert.ok(html.includes('!pendingImportDraft.edits.variant.trim()'));
});
test('confirmation submits edits once and updates the card status', async () => {
  const calls = [];
  let finish;
  const {state:s} = dashboard((...args) => {calls.push(args); return new Promise(resolve => {finish = resolve;});});
  const item = row();
  s.openPendingImport(item);
  s.pendingImportDraft.edits.description = '改文';
  s.pendingImportDraft.edits.variant = 'A';
  const request = s.approvePendingImport();
  await s.approvePendingImport();
  s.cancelPendingImport();
  assert.ok(s.pendingImportDraft);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][0], '/api/admin/pending-alarm-imports/1');
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0][1])), {action:'accept', edits:{description:'改文', variant:'A'}});
  finish({ok:true, json:async () => ({status:'approved'})});
  await request;
  assert.equal(item.status, 'approved');
  assert.equal(item.busy, false);
  assert.equal(s.pendingImportDraft, null);
});
test('failed approval keeps edits and exposes the error for retry', async () => {
  for (const put of [async () => ({ok:false,json:async () => ({error:'核准失敗'})}), async () => {throw Error('offline');}]) {
    const {state:s} = dashboard(put);
    const item = row();
    s.openPendingImport(item);
    s.pendingImportDraft.edits.description = '改文';
    await s.approvePendingImport();
    assert.equal(s.pendingImportDraft.edits.description, '改文');
    assert.ok(item.rowError);
    assert.equal(item.busy, false);
    assert.equal(item.status, 'pending');
  }
});
