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
    document: {addEventListener() {}}, window: {addEventListener() {}, removeEventListener() {}}, localStorage: {getItem() {return null;}},
    AlarmApi: {post}, console, setTimeout: fakeSetTimeout, clearTimeout};
  for (const m of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
    // Vue 單元測試只載入應用；獨立登入 modal 由 test_deferred_auth_modal.cjs 覆蓋。
    if (m[1].includes('createApp')) vm.runInNewContext(m[1], context);
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
// bootstrap.min.css 沒有引入，代表 .modal { display:none } 這條基礎
// 規則本頁完全沒有來源。2026-09-29 正式環境 regression：對話框一
// 進入 DOM（使用者點開任一筆警報詳情，讓外層 v-if="selected" 為 true）
// 就是可見狀態，因為普通 <div> 沒有任何 display:none 的預設值，
// 看起來像「自動彈出」且無法透過取消鈕關閉（見下面 null 防呆測試）。
test('issue-report-modal has its own display:none / .show CSS since bootstrap.min.css is not loaded', () => {
  assert.match(html, /\.issue-report-modal\s*\{[^}]*display:\s*none[^}]*\}/,
    '.issue-report-modal 本身需要顯式 display:none，否則巢狀在 v-if="selected" 底下的這個 <div> 一旦渲染進 DOM 就會直接可見');
  assert.match(html, /\.issue-report-modal\.show\s*\{[^}]*display:\s*block[^}]*\}/,
    'bootstrap.Modal 的 show()/hide() 是靠切換 .show class 運作，這裡要有對應的 CSS 才會真的顯示/隱藏');
});
test('closeDataIssueModal() does not throw when dataIssueModal is still null', () => {
  // 2026-09-29 regression 的直接成因：對話框因為缺 display:none CSS
  // 而意外可見時，使用者點的「取消」按鈕呼叫 closeDataIssueModal()，
  // 但 openDataIssueReport()（唯一會建構 dataIssueModal 的地方）從未
  // 被呼叫過，dataIssueModal 仍是 null，對 null 呼叫 .hide() 直接
  // 拋錯、Vue 事件處理中斷，畫面卡死關不掉——這正是使用者回報「點
  // 取消/X都關不掉」的根因。
  const s = page();
  s.dataIssueModal = null;
  assert.doesNotThrow(() => s.closeDataIssueModal());
});
// 以下兩個測試不用 page() fixture（它為了其他測試方便直接塞假
// dataIssueModal，繞過了真正的 new bootstrap.Modal(...) 呼叫路徑）。
// 這裡改用真的 mounted()，並用一個會在建構時丟例外的假 bootstrap.Modal
// 模擬「el 是 undefined 時 Bootstrap 內部會炸」的真實情況（實際案例：
// dataIssueModalEl 巢狀在外層 v-if="selected" 底下，頁面剛載入、使用者
// 還沒點開任何警報時 selected 是 null，該區塊沒有渲染進 DOM，
// $refs.dataIssueModalEl 是 undefined，2026-09-29 造成正式環境
// TypeError: Cannot read properties of undefined (reading 'backdrop')，
// 整頁查詢功能連帶壞掉，已用headless Chrome實際重現、修復後複測零錯誤，
// 見對話紀錄）。
function pageWithRealMount(post, {modalCtor, refsAtMount} = {}) {
  let component;
  const app = {directive() {return app;}, mount() {}};
  // dataIssueModalEl 巢狀在 v-if="selected" 底下，selected 初始值是
  // null，所以 mounted() 執行的當下這個 ref 本來就該是 undefined
  // （不是某個現成的 DOM element）——這才是真實情況，不是為了測試
  // 方便而簡化。呼叫端可透過 refsAtMount 覆寫來測「DOM 已渲染」的情境。
  const refs = refsAtMount !== undefined ? refsAtMount : {dataIssueModalEl: undefined};
  const ModalCtor = modalCtor || class {
    constructor(el) {
      if (!el) throw new TypeError("Cannot read properties of undefined (reading 'backdrop')");
      this.el = el; this.shown = false;
    }
    show() { this.shown = true; }
    hide() { this.shown = false; }
  };
  const context = {Vue: {createApp(c) {component = c; return app;}}, navigator: {},
    document: {addEventListener() {}}, window: {addEventListener() {}, removeEventListener() {}}, localStorage: {getItem() {return null;}},
    AlarmApi: {post, whoami: async () => ({auth:false, department:null}), get: async () => ({ok:true, json: async () => ([])})},
    bootstrap: {Modal: ModalCtor}, console, setTimeout: () => {}, clearTimeout};
  for (const m of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
    // Vue 單元測試只載入應用；獨立登入 modal 由 test_deferred_auth_modal.cjs 覆蓋。
    if (m[1].includes('createApp')) vm.runInNewContext(m[1], context);
  }
  const s = component.data();
  for (const [k, v] of Object.entries(component.methods)) s[k] = v.bind(s);
  s.$refs = refs;
  s.selected = {department:'line 3', device_model:'M', code:'E', variant:'variant A'};
  s.whoami = {department:null};
  return {s, mounted: component.mounted ? component.mounted.bind(s) : null, ModalCtor};
}
test('mounted() does not construct bootstrap.Modal while el is still undefined (regression repro)', () => {
  // refs.dataIssueModalEl 是 undefined（模擬 v-if="selected" 為 false
  // 時該區塊沒有渲染進 DOM），如果 mounted() 呼叫 new bootstrap.Modal
  // 一定會炸出跟正式環境同樣的 TypeError。
  const {s, mounted} = pageWithRealMount(async () => ({ok:true}));
  assert.doesNotThrow(() => mounted && mounted(),
    'mounted() 不該在 el 還是 undefined 時就建構 bootstrap.Modal');
  assert.equal(s.dataIssueModal, null);
});
test('openDataIssueReport() constructs the modal lazily on first call, using the real ref (now populated), and reuses it on later calls', () => {
  let constructCount = 0;
  class CountingModal {
    constructor(el) {
      if (!el) throw new TypeError("Cannot read properties of undefined (reading 'backdrop')");
      constructCount++; this.el = el; this.shown = false;
    }
    show() { this.shown = true; }
    hide() { this.shown = false; }
  }
  // openDataIssueReport() 只會在使用者已經選取一筆警報（v-if="selected"
  // 為 true）之後才可能被呼叫，這時候 dataIssueModalEl 已經渲染進 DOM，
  // 所以這裡的 fixture 給一個真實存在的 ref，跟上一個測試的「mounted
  // 時還沒渲染」情境刻意不同。
  const {s} = pageWithRealMount(async () => ({ok:true}),
    {modalCtor: CountingModal, refsAtMount: {dataIssueModalEl: {tagName: 'DIV'}}});
  s.dataIssueModal = null;
  s.openDataIssueReport();
  assert.equal(constructCount, 1);
  assert.equal(s.dataIssueModal.shown, true);
  s.closeDataIssueModal();
  s.openDataIssueReport();
  assert.equal(constructCount, 1, '第二次開啟不該重新 new，應該重用同一個實例');
  assert.equal(s.dataIssueModal.shown, true);
});
