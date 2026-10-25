// Dependency-free regression tests for the actual inline UI script.
// Run: node --test tests/ui_state.cjs (no browser/npm packages required).
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function setup() {
  const html = fs.readFileSync(process.env.PAPERDROP_UI_HTML || path.join(__dirname, '../src/paperdrop/assets/ui.html'), 'utf8');
  class Element {
    constructor(tag, attrs = '') {
      this.tagName = tag; this.dataset = {}; this.listeners = {}; this.disabled = /\bdisabled\b/.test(attrs);
      this.value = ''; this.textContent = ''; this.options = [];
      for (const [, key, value] of attrs.matchAll(/([\w-]+)="([^"]*)"/g)) {
        if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
        else this[key] = value;
      }
      const classes = new Set();
      this.classList = {add: x => classes.add(x), remove: x => classes.delete(x),
        toggle: (x, on) => on ? classes.add(x) : classes.delete(x)};
    }
    set innerHTML(value) {
      this.html = value;
      if (this.tagName === 'select') {
        this.options = [...value.matchAll(/<option\b([^>]*)>(.*?)<\/option>/g)].map(([, attrs, text]) => {
          const option = new Element('option', attrs); option.textContent = text; return option;
        });
        this.value = this.options[0]?.value || '';
      }
    }
    get innerHTML() { return this.html || ''; }
    setAttribute(key, value) { this[key] = value; }
    addEventListener(event, fn) { (this.listeners[event] ||= []).push(fn); }
    dispatch(event) { for (const fn of this.listeners[event] || []) fn(); }
  }
  const elements = [...html.matchAll(/<(\w+)\b([^>]*)>/g)].map(([, tag, attrs]) => new Element(tag, attrs));
  const ids = Object.fromEntries(elements.filter(e => e.id).map(e => [e.id, e]));
  const requests = [];
  const status = {version: 'test', config_path: '/fixture/config.json', platform: 'test',
    source: {url: 'https://fixture.invalid/a.json'}, schedule: {daily_time: '07:35'}, schedule_state: '已启用'};
  const sandbox = {URLSearchParams, location: {search: '?t=test'}, confirm: () => true,
    setTimeout: () => 0, localStorage: {getItem: () => null, setItem() {}},
    document: {documentElement: new Element('html'), getElementById: id => ids[id],
      querySelectorAll: selector => elements.filter(e => selector.split(',').some(s => {
        s = s.trim();
        if (s === '[data-zh][data-en]') return e.dataset.zh !== undefined && e.dataset.en !== undefined;
        if (s === '[data-lang]') return e.dataset.lang !== undefined;
        if (s === '[data-theme-choice]') return e.dataset.themeChoice !== undefined;
        return e.tagName === s;
      }))},
    fetch: (url, options) => new Promise((resolve, reject) => requests.push({url, options, reject,
      respond: data => resolve({ok: true, json: async () => data})}))};
  const context = vm.createContext(sandbox);
  const script = html.match(/<script>([\s\S]*?)<\/script>/)[1].replace('__PRESETS__', JSON.stringify([
    ['A', status.source.url, 'A EN'], ['B', 'https://fixture.invalid/b.json', 'B EN']]));
  vm.runInContext(script, context);
  // Initial status is installed synchronously; all subsequent requests stay deferred.
  vm.runInContext(`renderStatus(${JSON.stringify(status)})`, context);
  const run = code => vm.runInContext(code, context);
  const snapshot = () => JSON.parse(run(`JSON.stringify({time:$('time').value,feed:$('feed').value,
    url:$('custom-url').value,source:selectedSource(),mode:CUSTOM_MODE,preview:PREVIEWED,
    disabled:$('apply').disabled,busy:BUSY})`));
  return {run, snapshot, requests, ids, status};
}

for (const custom of [false, true]) {
  for (const checked of [false, true]) {
    test(`language preserves ${custom ? 'custom' : 'preset'} ${checked ? 'checked' : 'unchecked'} draft`, () => {
      const ui = setup();
      ui.run(`$('time').value='06:15';$('time').dispatch('input');$('feed').value='1';
        $('feed').dispatch('change');$('custom-url').value='https://fixture.invalid/custom.json';
        ${custom ? "$('custom-toggle').onclick();$('custom-url').value='https://fixture.invalid/custom.json';$('custom-url').dispatch('input');" : ''}`);
      if (checked) ui.run(`renderCheck({ok:true,source_check:{ok:true},changes:[['time','07:35','06:15']]},
        {daily_time:$('time').value,source_url:selectedSource()})`);
      const before = ui.snapshot();
      for (const language of ['en', 'zh']) {
        ui.run(`setLanguageAndOptions('${language}')`);
        assert.deepEqual(ui.snapshot(), before);
      }
      assert.equal(ui.ids.feed.options[1].textContent, 'B');
    });
  }
}

const passedCheck = {ok: true, source_check: {ok: true}, changes: [['time', '07:35', '06:15']]};
function draft(ui, custom = false) {
  ui.run(`$('time').value='06:15';$('time').dispatch('input');$('feed').value='1';$('feed').dispatch('change')`);
  if (custom) ui.run(`$('custom-toggle').onclick();$('custom-url').value='https://fixture.invalid/b.json';$('custom-url').dispatch('input')`);
}
function beginCheck(ui) {
  const done = ui.run(`$('check').onclick()`);
  return {done, request: ui.requests.at(-1)};
}

// Dispatch events even on disabled fields: disabling the UI is not a response guard.
const edits = {
  preset: `$('feed').value='0';$('feed').dispatch('change')`,
  time_input: `$('time').value='05:10';$('time').dispatch('input')`,
  time_change: `$('time').value='05:10';$('time').dispatch('change')`,
  custom_url: `$('custom-url').value='https://fixture.invalid/c.json';$('custom-url').dispatch('input')`,
  source_mode: `$('preset-toggle').onclick()`,
  mode_round_trip: `$('preset-toggle').onclick();$('custom-toggle').onclick();$('custom-url').value='https://fixture.invalid/b.json'`,
  edit_round_trip: `$('time').value='05:10';$('time').dispatch('input');$('time').value='06:15';$('time').dispatch('input')`,
  restore: `$('restore').onclick()`,
  silent_time: `$('time').value='05:10'`,
  silent_url: `$('custom-url').value='https://fixture.invalid/c.json'`,
};
for (const [name, edit] of Object.entries(edits)) {
  test(`late check cannot revive preview after ${name}`, async () => {
    const ui = setup(); draft(ui, true);
    if (name === 'preset') ui.run(`$('preset-toggle').onclick()`);
    const {done, request} = beginCheck(ui);
    ui.run(edit);
    request.respond(passedCheck); await done;
    assert.equal(ui.snapshot().preview, null);
    assert.equal(ui.snapshot().disabled, true);
    const count = ui.requests.length;
    await ui.run(`$('apply').onclick()`);
    assert.equal(ui.requests.length, count, 'no stale /api/apply request');
  });
}

for (const statusFirst of [false, true]) {
  for (const fails of [false, true]) {
    test(`refresh invalidates pending check (${statusFirst ? 'status' : 'check'} first, status ${fails ? 'fails' : 'passes'})`, async () => {
      const ui = setup(); draft(ui);
      const {done, request} = beginCheck(ui);
      const refresh = ui.run(`$('refresh').onclick()`);
      const statusRequest = ui.requests.at(-1);
      const finishStatus = async () => {
        if (fails) statusRequest.reject(new Error('fixture status failure'));
        else statusRequest.respond(ui.status);
        await refresh;
      };
      if (statusFirst) await finishStatus();
      request.respond(passedCheck); await done;
      assert.equal(ui.snapshot().preview, null);
      assert.equal(ui.snapshot().disabled, true);
      const count = ui.requests.length;
      await ui.run(`$('apply').onclick()`);
      assert.equal(ui.requests.length, count);
      if (!statusFirst) await finishStatus();
    });
  }
}

test('late status cannot overwrite a newer draft or check', async () => {
  const ui = setup();
  const refresh = ui.run(`loadStatus()`);
  const request = ui.requests.at(-1);
  draft(ui);
  const check = beginCheck(ui); check.request.respond(passedCheck); await check.done;
  const before = ui.snapshot();
  request.respond(ui.status); await refresh;
  assert.deepEqual(ui.snapshot(), before);
});

test('older refresh cannot overwrite the latest status', async () => {
  const ui = setup();
  const older = ui.run(`loadStatus()`); const oldRequest = ui.requests.at(-1);
  const newer = ui.run(`loadStatus()`); const newRequest = ui.requests.at(-1);
  const status = {...ui.status, schedule: {daily_time: '09:10'}};
  newRequest.respond(status); await newer;
  oldRequest.respond(ui.status); await older;
  assert.equal(ui.snapshot().time, '09:10');
});

test('busy locks all configuration controls and rejects apply', async () => {
  const ui = setup(); draft(ui, true);
  ui.run(`renderCheck(${JSON.stringify(passedCheck)}, {daily_time:$('time').value,source_url:selectedSource()})`);
  const {done, request} = beginCheck(ui);
  assert.equal(ui.snapshot().preview, null, 'new check consumes old preview');
  for (const id of ['time', 'feed', 'custom-url', 'custom-toggle', 'preset-toggle', 'apply']) {
    assert.equal(ui.ids[id].disabled, true, id);
  }
  const count = ui.requests.length;
  await ui.run(`$('apply').onclick()`);
  assert.equal(ui.requests.length, count);
  request.respond(passedCheck); await done;
  for (const id of ['time', 'feed', 'custom-url']) assert.equal(ui.ids[id].disabled, false, id);
  assert.equal(ui.snapshot().disabled, false, 'fresh matching check remains applicable');
});

for (const custom of [false, true]) {
  test(`language and theme during ${custom ? 'custom' : 'preset'} check preserve matching payload`, async () => {
    const ui = setup(); draft(ui, custom);
    const {done, request} = beginCheck(ui);
    const before = ui.snapshot();
    ui.run(`setLanguageAndOptions('en');setTheme('dark')`);
    assert.deepEqual(ui.snapshot(), before);
    request.respond(passedCheck); await done;
    assert.deepEqual(ui.snapshot().preview, {daily_time: before.time, source_url: before.source});
    assert.equal(ui.snapshot().disabled, false);
  });
}

test('apply revalidates the visible payload even without an input event', async () => {
  const ui = setup(); draft(ui);
  const check = beginCheck(ui); check.request.respond(passedCheck); await check.done;
  ui.run(`$('time').value='05:10'`);
  const count = ui.requests.length;
  const applying = ui.run(`$('apply').onclick()`);
  assert.equal(ui.requests.length, count, 'must not post checked-but-no-longer-visible values');
  await applying;
  assert.equal(ui.snapshot().preview, null);
  assert.equal(ui.snapshot().disabled, true);
});
