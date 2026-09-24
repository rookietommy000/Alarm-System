const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(require('node:path').join(__dirname, '../frontend/index.html'), 'utf8');
function page(post) {
  let component;
  const app = {directive() {return app;}, mount() {}};
  const context = {Vue: {createApp(c) {component = c; return app;}}, navigator: {},
    document: {addEventListener() {}}, window: {}, localStorage: {getItem() {return null;}},
    AlarmApi: {post}, console, setTimeout, clearTimeout};
  for (const m of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
    if (m[1].trim()) vm.runInNewContext(m[1], context);
  }
  const s = component.data();
  for (const [k, v] of Object.entries(component.methods)) s[k] = v.bind(s);
  s.selected = {department:'line 3', device_model:'M', code:'E', variant:'variant A'};
  s.whoami = {department:null};
  s.$refs = {dataIssueDialog: {showModal() {}, close() {s.closed = true;}}};
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
  assert.equal(s.closed, true);
  assert.equal(s.issueSending, false);
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
