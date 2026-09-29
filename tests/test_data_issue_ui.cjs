const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(require('node:path').join(__dirname, '../frontend/index.html'), 'utf8');
function page(post) {
  let component;
  const app = {directive() {return app;}, mount() {}};
  const timers = [];
  const fakeSetTimeout = (fn, ms) => {timers.push(fn); return timers.length;};
  const context = {Vue: {createApp(c) {component = c; return app;}}, navigator: {},
    document: {addEventListener() {}}, window: {}, localStorage: {getItem() {return null;}},
    AlarmApi: {post}, console, setTimeout: fakeSetTimeout, clearTimeout};
  for (const m of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
    if (m[1].trim()) vm.runInNewContext(m[1], context);
  }
  const s = component.data();
  for (const [k, v] of Object.entries(component.methods)) s[k] = v.bind(s);
  s.selected = {department:'line 3', device_model:'M', code:'E', variant:'variant A'};
  s.whoami = {department:null};
  s.dataIssueModal = {shown: false, show() {this.shown = true;}, hide() {this.shown = false; s.closed = true;}};
  s._runPendingTimers = () => { for (const fn of timers.splice(0)) fn(); };
  return s;
}
test('source template hides absent source and absent date, interpolates escaped text', () => {
  const fragment = html.match(/<template v-if="selected.import_source">([\s\S]*?)<\/template>/)[1];
  assert.match(fragment, /<dt>資料來源<\/dt>/);
  assert.match(fragment, /{{ selected.import_source }}/);
  assert.match(fragment, /v-if="selected.imported_at"/);
  assert.match(fragment, /{{ fmtLocalTime\(selected.imported_at\) }}/);
  assert.ok(!fragment.includes('v-html'));
  const s = page();
  assert.equal(s.fmtLocalTime(undefined), '—');
  assert.ok(!s.fmtLocalTime('2026-09-24T01:00:00Z').includes('undefined'));
});
test('submit uses selected department and full PK, success after response only', async () => {
  const calls = [];
  const s = page(async (...args) => {calls.push(args); return {ok:true};});
  s.openDataIssueReport();
  s.issueContent = ' wrong cause ';
  await s.submitDataIssueReport();
  assert.equal(calls[0][0], '/api/data-issue-reports/line%203');
  assert.deepEqual(JSON.parse(JSON.stringify(calls[0][1])), {device_model:'M', code:'E', variant:'variant A', content:'wrong cause'});
  assert.equal(s.issueSent, true);
  assert.equal(s.issueSending, false);
  // 成功後不立即關閉，讓使用者先看到成功提示，稍後才自動關閉（用假
  // timer 驗證有排程關閉動作，不真的等待，避免拖慢測試套件）。
  assert.equal(s.closed, undefined);
  s._runPendingTimers();
  assert.equal(s.closed, true);
});
test('failed submission keeps form and content for retry', async () => {
  const s = page(async () => ({ok:false,json:async () => ({error:'儲存失敗'})}));
  s.openDataIssueReport(); s.issueContent = 'wrong cause';
  await s.submitDataIssueReport();
  assert.equal(s.issueSent, false);
  assert.equal(s.closed, undefined);
  assert.equal(s.issueContent, 'wrong cause');
  assert.equal(s.issueError, '儲存失敗');
  assert.equal(s.issueSending, false);
});
test('missing department, empty content, duplicate submit do not send', async () => {
  let count = 0;
  const s = page(async () => {count++;});
  s.openDataIssueReport();
  await s.submitDataIssueReport();
  s.issueContent = 'wrong'; s.issueAlarm.department = null;
  await s.submitDataIssueReport();
  s.issueAlarm.department = 'line 3'; s.issueSending = true;
  await s.submitDataIssueReport();
  assert.equal(count, 0);
});
test('report button requires login, matching other gated actions', () => {
  const fragment = html.match(/<button[^>]*@click="openDataIssueReport"[^>]*>/)[0];
  assert.match(fragment, /v-if="whoami\.auth && whoami\.department"/);
});
test('expired session on submit surfaces a login-specific message, not a generic failure', async () => {
  const s = page(async () => ({ok:false, status:401, json:async () => ({error:'未授權'})}));
  s.openDataIssueReport(); s.issueContent = 'wrong cause';
  await s.submitDataIssueReport();
  assert.equal(s.issueSent, false);
  assert.match(s.issueError, /登入/);
});
test('success and failure alerts are visually distinguished with a dedicated class', () => {
  const successFragment = html.match(/<p v-if="issueSent"[^>]*>/)[0];
  assert.match(successFragment, /class="issue-alert issue-alert-success"/);
  const errorFragment = html.match(/<p v-if="issueError"[^>]*>/)[0];
  assert.match(errorFragment, /class="issue-alert issue-alert-danger"/);
});
test('modal only borrows bootstrap.bundle.min.js behaviour, not the full bootstrap.min.css', () => {
  assert.match(html, /<script src="https:\/\/cdn\.jsdelivr\.net\/npm\/bootstrap@[^"]*\/bootstrap\.bundle\.min\.js"><\/script>/);
  assert.ok(!/<link[^>]*bootstrap\.min\.css/.test(html),
    'index.html 不應引入 bootstrap.min.css 的 <link>，避免全域樣式污染既有頁面（設計理由可以出現在註解裡，不代表真的引入）');
});
