const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const html = fs.readFileSync(require('node:path').join(__dirname, '../frontend/index.html'), 'utf8');
function page(source = html, documentOverrides = {}) {
  let component;
  const app = {directive() {return app;}, mount() {}};
  const context = {Vue: {createApp(c) {component = c; return app;}}, navigator: {},
    document: {addEventListener() {}, ...documentOverrides}, window: {}, localStorage: {getItem() {return null;}},
    AlarmApi: {}, console, setTimeout: () => {}, clearTimeout: () => {}};
  for (const m of source.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)) {
    // Vue 單元測試只載入應用；獨立登入 modal 由 test_deferred_auth_modal.cjs 覆蓋。
    if (m[1].includes('createApp')) vm.runInNewContext(m[1], context);
  }
  const s = component.data();
  for (const [k, v] of Object.entries(component.methods)) s[k] = v.bind(s);
  return s;
}

// B1：拍照上傳前縮放到長邊 1280px 以內，省下 /api/analyze 的傳輸量、
// 同時解掉大圖片 base64 塞爆記憶體的 OOM 風險（見 CLAUDE.md）。這裡只
// 測縮放算式本身（scaledPhotoSize），並以 canvas/video stub 驗證
// takePhoto() 傳入的尺寸與 JPEG 品質；不驗證瀏覽器實際編碼的影像。

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

test('configured PHOTO_MAX_EDGE is respected', () => {
  const s = page(html.replace('const PHOTO_MAX_EDGE = 1280;', 'const PHOTO_MAX_EDGE = 500;'));
  const { width, height } = s.scaledPhotoSize(2000, 1000);
  assert.equal(width, 500);
  assert.equal(height, 250);
});

for (const quality of [0.7, 0.9]) {
  test(`takePhoto uses configured JPEG quality ${quality} and scaled dimensions`, async () => {
    const calls = [];
    const canvas = {
      getContext() { return {drawImage(...args) { calls.push(args); }}; },
      toDataURL(type, q) {
        assert.equal(type, 'image/jpeg');
        assert.equal(q, quality);
        return 'data:image/jpeg;base64,ZmFrZQ==';
      },
    };
    const s = page(html.replace('const PHOTO_JPEG_QUALITY = 0.7;',
      `const PHOTO_JPEG_QUALITY = ${quality};`), {
      createElement(tag) { assert.equal(tag, 'canvas'); return canvas; },
    });
    const video = {readyState: 2, videoWidth: 4032, videoHeight: 3024};
    s.$refs = {videoEl: video};
    s.stopCamera = () => {};
    await s.takePhoto();
    assert.equal(canvas.width, 1280);
    assert.equal(canvas.height, 960);
    assert.deepEqual(calls, [[video, 0, 0, 1280, 960]]);
    assert.equal(s.pendingImageB64, 'ZmFrZQ==');
    assert.equal(s.pendingMimeType, 'image/jpeg');
  });
}
