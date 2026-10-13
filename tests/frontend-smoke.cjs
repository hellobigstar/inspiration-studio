#!/usr/bin/env node
'use strict';

// Dependency-free checks for the actual inline application script.
// This minimal DOM verifies behavior; it does not replace browser visual QA.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '..', 'studio', 'index.html'), 'utf8');
const source = html.match(/<script>\s*([\s\S]*?)\s*<\/script>/)[1];
new vm.Script(source, { filename: 'studio/index.html' });
assert.match(html, /<title>灵感画室/);
assert.match(html, /id="backend-link"[^>]*hidden/);
assert.doesNotMatch(html, /启动千问|千问画室|:8189|qwen-studio-drafts/);
assert.doesNotMatch(html, /<(?:script|link)\b[^>]*(?:src|href)="https?:/);

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase();
    this.value = '';
    this.checked = false;
    this.hidden = false;
    this.disabled = false;
    this.children = [];
    this.dataset = {};
    this.style = {};
    this.attributes = {};
    this.listeners = {};
    const classes = new Set();
    this.classList = {
      add: name => classes.add(name),
      remove: name => classes.delete(name),
      toggle: (name, value) => value ? classes.add(name) : classes.delete(name),
    };
  }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  removeAttribute(key) { delete this.attributes[key]; delete this[key]; }
  addEventListener(name, handler) { (this.listeners[name] ??= []).push(handler); }
  replaceChildren(...children) { this.children = children; }
  append(...children) { this.children.push(...children); }
  focus() { this.focused = true; }
  fire(name) { for (const handler of this.listeners[name] || []) handler({ target: this, preventDefault() {}, stopPropagation() {} }); }
  click() { this.fire('click'); }
}

async function createApp(saved, status = { ready: true, backend: 'http://127.0.0.1:9001', busy: false }) {
  const elements = new Map();
  for (const match of html.matchAll(/<([\w-]+)\b([^>]*\bid="([^"]+)"[^>]*)>/g)) {
    const element = new Element(match[1]);
    element.hidden = /\bhidden\b/.test(match[2]);
    elements.set(match[3], element);
  }
  const modes = ['text', 'reference', 'edit'].map(mode => {
    const button = elements.get('mode-' + mode);
    button.dataset.mode = mode;
    return button;
  });
  const radios = [1024, 768].map(value => Object.assign(new Element('input'), { value: String(value), checked: value === 1024 }));
  const storage = new Map(saved == null ? [] : [['inspiration-studio-drafts', typeof saved === 'string' ? saved : JSON.stringify(saved)]]);
  const intervals = [];
  const state = { status, offline: false };
  const context = vm.createContext({
    URL, console,
    location: { href: 'http://127.0.0.1:8190/' },
    document: {
      getElementById(id) { assert.ok(elements.has(id), 'Missing element: ' + id); return elements.get(id); },
      querySelector(selector) { assert.equal(selector, '[name=resolution]:checked'); return radios.find(input => input.checked); },
      querySelectorAll(selector) { assert.ok(['.mode', '[name=resolution]'].includes(selector)); return selector === '.mode' ? modes : radios; },
      createElement(tag) { return new Element(tag); },
    },
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value) },
    setTimeout() { return 1; }, clearTimeout() {},
    setInterval(handler) { intervals.push(handler); return intervals.length; },
    async fetch(url) {
      if (state.offline) throw new Error('Offline');
      const data = url === '/api/status' ? state.status : url === '/api/jobs' ? { jobs: [] } : {};
      return { ok: true, json: async () => data };
    },
  });
  const instrumented = source.replace(/\}\)\(\);\s*$/, 'globalThis.appTest={safeBackendUrl,readPayload,refreshStatus,uploadFiles};})();');
  vm.runInContext(instrumented, context, { filename: 'studio/index.html' });
  await new Promise(resolve => setImmediate(resolve));
  return { context, elements, radios, storage, state, test: context.appTest };
}

(async () => {
  const app = await createApp({
    mode: 'edit',
    drafts: {
      text: { prompt: '文字草稿', keep: '', images: [], resolution: 1024, seed: '' },
      reference: { prompt: '参考草稿', keep: '', images: ['ref1', 'ref2'], resolution: 768, seed: '17' },
      edit: { prompt: '修改草稿', keep: '保留构图', images: ['ref1'], resolution: 1024, seed: '29' },
    },
    imageMap: { ref1: { id: 'ref1', url: '/media/input/ref1.png' }, ref2: { id: 'ref2', url: '/media/input/ref2.png' } },
  });
  const get = id => app.elements.get(id);
  const saved = () => JSON.parse(app.storage.get('inspiration-studio-drafts'));
  assert.equal(get('prompt').value, '修改草稿');
  assert.equal(get('keep').value, '保留构图');
  assert.equal(get('seed').value, '29');
  assert.equal(get('upload-area').hidden, false);
  assert.equal(get('keep-wrap').hidden, false);
  get('prompt').value = '新的修改草稿';
  get('seed').value = '31';
  app.radios.forEach(input => { input.checked = input.value === '768'; });
  get('mode-reference').click();
  assert.equal(get('prompt').value, '参考草稿');
  assert.equal(get('seed').value, '17');
  assert.equal(app.test.readPayload().resolution, 768);
  assert.deepEqual(Array.from(app.test.readPayload().images), ['ref1', 'ref2']);
  get('mode-text').click();
  assert.equal(get('upload-area').hidden, true);
  assert.equal(get('seed').value, '');
  assert.equal(app.test.readPayload().resolution, 1024);
  assert.deepEqual(Array.from(app.test.readPayload().images), []);
  get('mode-edit').click();
  assert.equal(get('prompt').value, '新的修改草稿');
  assert.equal(get('seed').value, '31');
  assert.equal(app.test.readPayload().resolution, 768);
  assert.equal(saved().drafts.reference.seed, '17');
  assert.equal(saved().drafts.text.resolution, 1024);
  assert.equal(saved().drafts.edit.resolution, 768);
  assert.equal(app.storage.has('qwen-studio-drafts'), false);

  for (const url of ['http://localhost:8188', 'http://127.0.0.1:9999/', 'http://[::1]:8188/', 'http://localhost/']) {
    assert.ok(app.test.safeBackendUrl(url), 'Accept loopback: ' + url);
  }
  for (const url of ['https://localhost:8188', 'http://example.com', 'http://localhost.example.com', 'http://127.0.0.2:8188', 'http://user@localhost:8188', 'http://@localhost:8188', 'http://localhost:8188@evil.test', 'http://localhost:8188/path', 'http://localhost:8188/?x=1', 'http://localhost:8188/#evil', '//localhost:8188', 'javascript:alert(1)', '', null, 123]) {
    assert.equal(app.test.safeBackendUrl(url), null, 'Reject unsafe link: ' + url);
  }
  assert.equal(get('backend-link').hidden, false);
  assert.equal(get('backend-link').href, 'http://127.0.0.1:9001/');
  app.state.status = { ready: false, backend: 'http://evil.test', issues: ['模型文件尚未下载。'], max_upload_mb: 8 };
  await app.test.refreshStatus();
  assert.equal(get('backend-link').hidden, true);
  assert.equal(get('backend-link').href, undefined);
  assert.equal(get('generate').disabled, true);
  assert.equal(get('generation-note').textContent, '模型文件尚未下载。');
  assert.match(get('upload-note').textContent, /8 MB/);
  await app.test.uploadFiles([{ name: 'large.png', type: 'image/png', size: 9 * 1024 * 1024 }], 0, 'edit');
  assert.match(get('error-box').textContent, /超过 8 MB/);
  app.state.offline = true;
  await app.test.refreshStatus();
  assert.equal(get('connection-text').textContent, '画室暂未连接');
  assert.match(get('generation-note').textContent, /start\.cmd/);

  const invalidJson = await createApp('{broken-json');
  assert.equal(invalidJson.elements.get('prompt').value, '');
  const invalidMode = await createApp({ mode: '__proto__', drafts: {} });
  assert.equal(invalidMode.elements.get('upload-area').hidden, true);
  console.log('PASS: syntax, branding, independent mode drafts, payloads, loopback links, offline issues, upload limit and corrupt storage.');
})().catch(error => { console.error(error); process.exitCode = 1; });
