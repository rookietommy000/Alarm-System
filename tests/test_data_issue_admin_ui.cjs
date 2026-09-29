// Run: node --test tests/test_data_issue_admin_ui.cjs (mocked API; no network).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

function dashboard(get) {
  let component;
  const html = fs.readFileSync(path.join(__dirname, '../frontend/dashboard.html'), 'utf8');
  const context = {
    Vue: {createApp(c) {component = c; return {mount() {}};}},
    AlarmApi: {get}, document: {addEventListener() {}},
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
  return {state, component};
}
const response = reports => ({ok: true, json: async () => ({reports})});

test('reports use selected department and clear data when all departments selected', async () => {
  const calls = [];
  const {state:s} = dashboard(async url => {calls.push(url); return response([{content:'回報'}]);});
  await s.loadDataIssueReports();
  assert.equal(calls[0], '/api/admin/data-issue-reports/line%2F2');
  assert.equal(s.dataIssueReports.items[0].content, '回報');
  s.viewDept = '__all__';
  await s.loadDataIssueReports();
  assert.equal(calls.length, 1);
  assert.equal(s.dataIssueReports.items.length, 0);
  assert.equal(s.dataIssueReports.loading, false);
});

// 這個保護來自 loadDataIssueReports() 每次呼叫開頭都重新賦值整個
// `this.dataIssueReports = {...}` 物件，不是顯式的 request token /
// AbortController 機制——舊請求 resolve 時寫入的是它呼叫當下閉包住
// 的舊物件參照，跟目前 `this.dataIssueReports` 指向的新物件不是同一個，
// UI 因此不會被過期回應覆蓋。這是「整個物件重新賦值」這個寫法帶來的
// 副作用，如果之後重構成「部分更新既有物件」（例如只改 items/loading
// 欄位而不整個重建），這個保護會悄悄消失，且不會有任何錯誤或警告，
// 只有這支測試會抓到（所以務必保留這個測試，不要因為「看起來多餘」
// 就刪掉）。
test('late response cannot replace reports from the current department', async () => {
  let resolveFirst;
  const {state:s} = dashboard(url => url.endsWith('line%2F2')
    ? new Promise(resolve => {resolveFirst = resolve;}) : Promise.resolve(response([{content:'new'}])));
  const first = s.loadDataIssueReports();
  s.viewDept = 'next';
  await s.loadDataIssueReports();
  resolveFirst(response([{content:'old'}]));
  await first;
  assert.equal(s.dataIssueReports.items[0].content, 'new');
});

test('API and network failures remain visible instead of appearing empty', async () => {
  for (const get of [async () => ({ok:false,json:async () => ({error:'讀取失敗'})}),
                     async () => {throw new Error('offline');}]) {
    const {state:s} = dashboard(get);
    await s.loadDataIssueReports();
    assert.ok(s.dataIssueReports.error);
    assert.equal(s.dataIssueReports.loading, false);
  }
});
