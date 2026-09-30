const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(require('node:path').join(__dirname, '../frontend/index.html'), 'utf8');
function page() {
  let component;
  const app = {directive() {return app;}, mount() {}};
  const context = {Vue: {createApp(c) {component = c; return app;}}, navigator: {},
    document: {addEventListener() {}}, window: {}, localStorage: {getItem() {return null;}},
    AlarmApi: {}, console, setTimeout: () => {}, clearTimeout: () => {}};
  for (const m of html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
    if (m[1].trim()) vm.runInNewContext(m[1], context);
  }
  const s = component.data();
  for (const [k, v] of Object.entries(component.methods)) s[k] = v.bind(s);
  return s;
}

// B1：拍照上傳前縮放到長邊 1280px 以內，省下 /api/analyze 的傳輸量、
// 同時解掉大圖片 base64 塞爆記憶體的 OOM 風險（見 CLAUDE.md）。這裡只
// 測縮放算式本身（scaledPhotoSize），不測 takePhoto() 整體——node --test
// 的沙箱沒有真實 canvas/video，測不到實際 drawImage/toDataURL 那段，
// 縮放邏輯本身是純數學、不碰 DOM，抽出來才能在這個沙箱裡驗證。

test('long edge above 1280 scales down, preserving aspect ratio', () => {
  const s = page();
  const { width, height } = s.scaledPhotoSize(4032, 3024); // 常見手機相機解析度，4:3
  assert.equal(width, 1280);
  assert.equal(height, 960); // 4032:3024 = 4:3，1280 * 3/4 = 960
});

test('portrait orientation scales by the taller edge (height), not width', () => {
  const s = page();
  const { width, height } = s.scaledPhotoSize(3024, 4032); // 直向拍攝
  assert.equal(height, 1280);
  assert.equal(width, 960);
});

test('image already smaller than 1280 is not upscaled', () => {
  const s = page();
  const { width, height } = s.scaledPhotoSize(640, 480); // 桌面測試常見假解析度
  assert.equal(width, 640);
  assert.equal(height, 480);
});

test('exactly 1280 long edge is unchanged', () => {
  const s = page();
  const { width, height } = s.scaledPhotoSize(1280, 720);
  assert.equal(width, 1280);
  assert.equal(height, 720);
});

test('custom maxEdge parameter is respected', () => {
  const s = page();
  const { width, height } = s.scaledPhotoSize(2000, 1000, 500);
  assert.equal(width, 500);
  assert.equal(height, 250);
});
