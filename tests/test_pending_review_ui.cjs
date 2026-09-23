// Run: node --test tests/test_pending_review_ui.cjs
// Execute the actual page scripts with mocked API/DOM; no CDN or production access.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function page(filename, options = {}) {
  let component;
  const location = new URL(options.url || 'http://localhost/admin/pending-review');
  const context = {
    Vue: {createApp(c) {component = c; return {mount() {}};}},
    URL, URLSearchParams, location, console, setTimeout, clearTimeout, navigator:{},
    history: {replaceState(_a, _b, url) {location.href = String(url);}},
    document: {addEventListener() {}, getElementById: options.getElementById || (() => null)},
    AlarmApi: options.api || {},
  };
  context.window = context;
  const html = fs.readFileSync(path.join(__dirname, '../frontend', filename), 'utf8');
  for (const match of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
    if (match[1].trim()) vm.runInNewContext(match[1], context);
  }
  const state = component.data();
  for (const [key, method] of Object.entries(component.methods)) state[key] = method.bind(state);
  for (const [key, getter] of Object.entries(component.computed)) Object.defineProperty(state, key, {get: () => getter.call(state)});
  return {state, component, location};
}
const rows = [
  {source_type:'suggestion',id:1,department:'line 2',code:'S',suggestion:'處置建議'},
  {source_type:'pending_import',id:1,department:'line 2',code:'I',flagged_reason:'格式錯誤'},
  {source_type:'semantic_review',review_index:7,code:'R',issue:'語意疑慮'}
];
const response = body => ({ok:true,status:200,json:async () => body});

test('three source filters and searchable summaries use real page methods', () => {
  const {state:s} = page('pending-review.html');
  s.items = rows;
  assert.equal(s.filtered.length,3);
  s.source = 'suggestion'; assert.equal(s.filtered[0].code,'S');
  s.source = ''; s.search = '格式錯誤'; assert.equal(s.filtered[0].code,'I');
  s.search = 'no match'; assert.equal(s.filtered.length,0);
});

test('links retain department, source and original semantic index; suggestions stay disabled', () => {
  const {state:s} = page('pending-review.html');
  s.whoami = {admin:true,superadmin:true};
  assert.equal(s.reviewLink(rows[0]),null);
  assert.equal(s.reviewLink(rows[2]),null);
  const link = new URL(s.reviewLink(rows[1]),'http://localhost');
  assert.equal(link.searchParams.get('dept'),'line 2');
  assert.equal(link.searchParams.get('page'),'pending-imports');
  assert.equal(link.hash,'#pending-import-1');
  s.department = 'line/2';
  const semantic = new URL(s.reviewLink(rows[2]),'http://localhost');
  assert.equal(semantic.searchParams.get('dept'),'line/2');
  assert.equal(semantic.hash,'#semantic-review-7');
  s.whoami = {admin:true,department:'local'};
  assert.equal(new URL(s.reviewLink(rows[2]),'http://localhost').searchParams.get('dept'),'local');
});

test('load scopes superadmin requests and changes department through URL', async () => {
  const calls = [];
  const {state:s,location} = page('pending-review.html', {url:'http://localhost/admin/pending-review?dept=line2',api:{
    whoami:async () => ({admin:true,superadmin:true}),
    get:async url => { calls.push(url); return response(url === '/api/admin/departments' ? [{id:'line2',name:'二部',active:true}] : {items:rows}); }
  }});
  await s.load();
  assert.equal(s.items.length,3);
  assert.equal(calls.at(-1),'/api/admin/pending-review?dept=line2');
  s.department = '__all__'; await s.changeDepartment();
  assert.equal(location.searchParams.get('dept'),'__all__');
  assert.equal(calls.at(-1),'/api/admin/pending-review?dept=__all__');
});

test('department user ignores URL scope and load failure clears stale rows', async () => {
  let fail = false;
  const calls = [];
  const {state:s} = page('pending-review.html', {url:'http://localhost/admin/pending-review?dept=other',api:{
    whoami:async () => ({admin:true,department:'local'}),
    get:async url => {calls.push(url); if(fail) throw new Error('offline'); return response({items:rows});}
  }});
  await s.load(); assert.equal(calls[0],'/api/admin/pending-review');
  fail = true; await s.load();
  assert.equal(s.error,'offline'); assert.equal(s.items.length,0); assert.equal(s.loading,false);
});

test('existing dashboard loaders focus deep-linked records and preserve API scope', async () => {
  const calls = [], focused = [];
  const {state:s,location} = page('dashboard.html', {url:'http://localhost/admin?dept=line2&page=pending-imports#pending-import-1',
    getElementById:id => ({scrollIntoView() {},focus() {focused.push(id);}}),
    api:{get:async url => {calls.push(url); return response(url.includes('semantic-review') ? {findings:[{status:'accepted'},{status:'pending'}]} : [rows[1]]);}}
  });
  s.whoami = {admin:true,superadmin:true}; s.viewDept = 'line2'; s.$nextTick = async () => {};
  await s.loadPendingImports();
  assert.equal(calls[0],'/api/admin/pending-alarm-imports?dept=line2');
  assert.equal(focused[0],'pending-import-1');
  location.hash = '#semantic-review-1';
  await s.loadSemanticReview();
  assert.equal(calls[1],'/api/admin/semantic-review/line2');
  assert.equal(s.semanticReview.items[1].idx,1);
  assert.equal(focused[1],'semantic-review-1');
});
