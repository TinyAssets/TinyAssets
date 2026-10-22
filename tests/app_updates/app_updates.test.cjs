const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../../tinyassets/onboarding/app_updates.js'), 'utf8');
const versions = require('../../tinyassets/onboarding/app_versions.json');

function page({ ua = '', native = false, version = '', bridge = true, platform = 'android' } = {}) {
  const elements = new Map();
  for (const id of ['app', 'app-update-banner', 'app-update-required',
    'app-update-required-open', 'app-update-banner-open', 'app-update-dismiss', 'app-update-error']) {
    elements.set(id, { hidden: true, inert: false, textContent: '', focus() {} });
  }
  const opened = [];
  const storage = new Map();
  const window = {
    open: url => opened.push(url),
    tinyassetsDesktop: !native && bridge ? { getVersion: async () => version, openExternal: async url => opened.push(url) } : undefined,
    Capacitor: native ? {
      isNativePlatform: () => true, getPlatform: () => platform,
      Plugins: { App: { getInfo: async () => ({ version }) },
        AppLauncher: { openUrl: async ({ url }) => { opened.push(url); return { completed: true }; } },
        Browser: { open: async ({ url }) => opened.push(url) } },
    } : undefined,
  };
  const context = { window, navigator: { userAgent: ua },
    document: { getElementById: id => elements.get(id) },
    localStorage: { getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value) } };
  vm.runInNewContext(`${source}\nthis.updates=AppUpdates`, context);
  return { updates: context.updates, elements, opened, storage, window };
}

test('numeric versions compare by component and invalid versions require an update', () => {
  const { updates } = page();
  assert.equal(updates.compare('1.0.10', '1.0.9'), 1);
  assert.equal(updates.compare('1.0', '1.0.0'), 0);
  assert.equal(updates.compare('1.0.1', '1.1.0'), -1);
  assert.equal(updates.state('', versions.android), 'required');
  assert.equal(updates.state('1.0.6', versions.android), 'required');
  assert.equal(updates.state('1.0.7', versions.android), 'none');
  assert.equal(updates.state('1.0.8', { ...versions.android, latest: '1.0.10' }), 'available');
});

test('old desktop without bridge is blocked and Update opens download page', async () => {
  const p = page({ ua: 'Electron/43 tinyassets-desktop/0.1.0', bridge: false });
  assert.equal(await p.updates.init(versions), false);
  assert.equal(p.elements.get('app-update-required').hidden, false);
  assert.equal(p.elements.get('app').inert, true);
  await p.elements.get('app-update-required-open').onclick();
  assert.equal(p.opened[0], versions.desktop.url);
});

test('the 0.1.1 desktop bridge without a version stays usable', async () => {
  const p = page({ ua: 'Electron/43', version: '' });
  delete p.window.tinyassetsDesktop.getVersion;
  assert.equal(await p.updates.init(versions), true);
  assert.equal(p.elements.get('app-update-required').hidden, true);
});

test('a newer desktop release produces a dismissible banner', async () => {
  const p = page({ ua: 'Electron/43', version: '0.1.1' });
  const policy = { ...versions, desktop: { ...versions.desktop, latest: '0.1.2' } };
  assert.equal(await p.updates.init(policy), true);
  assert.equal(p.elements.get('app-update-banner').hidden, false);
  await p.elements.get('app-update-banner-open').onclick();
  assert.equal(p.opened[0], versions.desktop.url);
});

test('available update can be dismissed and required update cannot', async () => {
  const p = page({ native: true, version: '1.0.7' });
  const policy = { ...versions, android: { ...versions.android, latest: '1.0.8' } };
  assert.equal(await p.updates.init(policy), true);
  assert.equal(p.elements.get('app-update-banner').hidden, false);
  await p.elements.get('app-update-banner-open').onclick();
  assert.equal(p.opened[0], 'market://details?id=io.tinyassets.app');
  p.elements.get('app-update-dismiss').onclick();
  assert.equal(p.elements.get('app-update-banner').hidden, true);
  assert.equal(p.storage.get('ta_update_dismissed_android'), '1.0.8');
  const old = page({ native: true, version: '1.0.6' });
  assert.equal(await old.updates.init(versions), false);
  assert.equal(old.elements.get('app-update-required').hidden, false);
});

test('Android falls back to HTTPS listing and iOS opens configured listing', async () => {
  const android = page({ native: true, version: '1.0.6' });
  android.window.Capacitor.Plugins.AppLauncher.openUrl = async () => ({ completed: false });
  await android.updates.init(versions);
  await android.elements.get('app-update-required-open').onclick();
  assert.equal(android.opened[0], versions.android.url);
  const ios = page({ native: true, platform: 'ios', version: '0.9.0' });
  await ios.updates.init(versions);
  await ios.elements.get('app-update-required-open').onclick();
  assert.equal(ios.opened[0], versions.ios.url);
});
