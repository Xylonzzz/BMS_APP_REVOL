const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

// 用内存 DOM 与可控制返回顺序的网络执行实际页面脚本。
// 测试不导出或替换生产代码中的实现函数。
class Element {
  constructor(tagName = 'div') {
    this.tagName = tagName; this.value = ''; this.dataset = {}; this.children = [];
    this.listeners = {}; this.attributes = {}; this.disabled = false; this.checked = false; this._text = '';
    const classes = new Set();
    this.classList = {add: (...x) => x.forEach(y => classes.add(y)), remove: (...x) => x.forEach(y => classes.delete(y)), contains: x => classes.has(x), toggle: (x, value = !classes.has(x)) => value ? classes.add(x) : classes.delete(x)};
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(x => x.textContent || '').join(''); }
  get options() { return this.children; }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  async emit(type) { await Promise.all((this.listeners[type] || []).map(fn => fn({target: this}))); }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this._text = ''; this.children = items; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
}
function snapshot(id = 'source', quality = 'valid') {
  const value = (n, physicalValid = true) => ({value: n, quality, valid: quality === 'valid', physicalValid, timestamp: 1000, sequence: 1, rawFrameIds: [0x104]});
  return {id, timestamp: 1000, dataSource: 'simulation', connectionState: quality === 'disconnected' ? 'disconnected' : 'connected', firmwareVersion: 'DEMO', totalVoltage: value(3800), current: value(3), cells: [{...value(3800), cellIndex: 0, moduleIndex: 0, channelIndex: 0, source: 'slave_01'}], temperatures: [], frames: [], diagnostics: [], modelDetection: {model: 'ef', mode: 'automatic', confidence: 'high', reason: 'ef_evidence', timestamp: 1000}};
}
function freeze(value) { Object.freeze(value); for (const item of Object.values(value)) if (item && typeof item === 'object' && !Object.isFrozen(item)) freeze(item); return value; }
const settle = () => new Promise(resolve => setImmediate(resolve));
async function harness(initial = snapshot()) {
  const html = fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8');
  const nodes = new Map();
  for (const match of html.matchAll(/<([a-z]+)\b([^>]+)>/g)) {
    const [, tag, attrs] = match; const id = attrs.match(/\bid="([^"]+)"/);
    if (!id) continue;
    const node = new Element(tag); nodes.set(`#${id[1]}`, node);
    node.value = attrs.match(/\bvalue="([^"]*)"/)?.[1] || '';
    node.checked = /\bchecked\b/.test(attrs);
  }
  nodes.set('.connection-pill', new Element());
  const documentListeners = {};
  const document = {body: new Element('body'), documentElement: new Element('html'), querySelector: s => nodes.get(s) || null, querySelectorAll: () => [], createElement: tag => new Element(tag), createElementNS: (_, tag) => new Element(tag), addEventListener(type, fn) { (documentListeners[type] ||= []).push(fn); }, dispatchEvent(event) { for (const fn of documentListeners[event.type] || []) fn(event); }};
  const requests = []; const intervals = new Map();
  let handler = async url => ({ok: true, json: async () => url.endsWith('/snapshots/latest') ? initial : {items: url.endsWith('/ai/providers') ? [{id: 'local', external: false}] : ['valid', 'disconnected_reconnect']}});
  const context = {document, window: {matchMedia: () => ({matches: false}), setInterval(fn, ms) { const id = intervals.size + 1; intervals.set(id, {fn, ms}); return id; }, clearInterval(id) {intervals.delete(id);}}, localStorage: {getItem: () => null, setItem() {}}, fetch: (...args) => {requests.push(args); return handler(...args);}, Option: function(text, value) {const e = new Element('option'); e.value = value; e.textContent = text; return e;}, CustomEvent: function(type, options) {this.type = type; this.detail = options?.detail;}, console, setTimeout, clearTimeout, AbortController};
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'modules.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'profiles.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(__dirname, 'app.js'), 'utf8'), context);
  await settle();
  return {node: s => nodes.get(s), requests, context, intervals, setFetch: fn => {handler = fn;}, async tick() {await Promise.all([...intervals.values()].map(timer => timer.fn())); await settle();}, async click(s) {assert.ok(nodes.has(s), `Missing ${s} control`); await nodes.get(s).emit('click'); await settle();}, async change(s, value) {nodes.get(s).value = value; await nodes.get(s).emit('change'); await settle();}, loadAi() {vm.runInContext(fs.readFileSync(path.join(__dirname, 'ai.js'), 'utf8'), context);}, async loadProduct() {vm.runInContext(fs.readFileSync(path.join(__dirname, 'product.js'), 'utf8'), context); await settle();}};
}
const reply = data => ({ok: true, json: async () => data});
const descendants = node => node.children.flatMap(child => [child, ...descendants(child)]);
const cellButtons = node => descendants(node).filter(child => child.className?.split(' ').includes('cell'));

test('product pages render scaled telemetry, searchable field evidence and honest missing parameters', async () => {
  const source = snapshot('product'); source.packStatus = {totalVoltageAdc: {value: 506.8, quality: 'unknown', valid: false, unit: 'V'}};
  source.signals = {chargerVoltage: {value: 5201, quality: 'unknown', valid: false}, relayPositive: {value: 255, quality: 'unknown', valid: false}, initFlag: {value: 1, quality: 'unknown', valid: false}};
  source.temperatures = Array.from({length: 48}, (_, i) => ({value: i === 7 ? null : 25, quality: 'unknown', valid: false}));
  const app = await harness(source); const catalog = {chargerVoltage: {label: '充电机反馈电压', path: 'A03.charge.ChargeVol_rx', divisor: 10, unit: 'V'}, totalVoltageAdc: {label: '电池总压', path: 'A03.State.TotalVoltage', divisor: 10, unit: 'V'}, 'threshold.Cell_OV_LV1': {label: '单体过压 LV1', path: 'A03.Threshold.Cell_OV_LV1', divisor: 1, unit: 'mV'}, 'alarm.OV': {label: '单体过压', path: 'A03.Alarm.OV'}};
  app.setFetch(async url => reply(url.endsWith('/catalog') ? {items: catalog} : {items: []})); await app.loadProduct();
  assert.match(app.node('#charger-grid').textContent, /520\.1 V/); assert.match(app.node('#relay-grid').textContent, /异常状态 · 255/);
  assert.match(app.node('#temperature-summary').textContent, /47 \/ 48.*0 有效/); assert.match(app.node('#threshold-table').textContent, /未收到/);
  assert.match(app.node('#alarm-summary').textContent, /0 \/ 1 已收到 · 0 非零/);
  app.node('#catalog-search').value = 'TotalVoltage'; await app.node('#catalog-search').emit('input');
  assert.match(app.node('#catalog-tree').textContent, /506\.8 V/); assert.doesNotMatch(app.node('#catalog-tree').textContent, /ChargeVol_rx/);
});

test('session replay crosses bounded pages, keeps poller paused, and charts only loaded records', async () => {
  const app = await harness(); const session = {id: 'current', name: 'field-session', source: 'file', first: 1000, last: 1101};
  const records = Array.from({length: 101}, (_, index) => { const s = snapshot(`session-${index}`); s.timestamp += index; s.packStatus = {current: {value: index, valid: false, quality: 'unknown'}}; return s; });
  app.setFetch(async url => {
    if (url.endsWith('/catalog')) return reply({items: {}});
    if (url.endsWith('/sessions')) return reply({items: [session]});
    if (url.includes('offset=100')) return reply({items: [records[100]], nextOffset: null});
    if (url.includes('offset=0')) return reply({items: records.slice(0, 100), nextOffset: 100});
    throw new Error(`unexpected ${url}`);
  });
  await app.loadProduct();
  const load = descendants(app.node('#session-list')).find(node => node.tagName === 'button'); await load.emit('click');
  assert.match(app.node('#replay-position').textContent, /记录 1 · 本页 1 \/ 100/);
  for (let i = 0; i < 100; i++) await app.click('#replay-next');
  assert.match(app.node('#replay-position').textContent, /记录 101 · 本页 1 \/ 1/);
  assert.match(app.node('#trend-caption').textContent, /记录 101–101/);
  const before = app.requests.length; await app.tick(); assert.equal(app.requests.length, before);
  await app.click('#replay-prev'); assert.match(app.node('#replay-position').textContent, /记录 100 · 本页 100 \/ 100/);
  assert.match(app.node('#trend-caption').textContent, /记录 1–100/);
  const exports = descendants(app.node('#session-list')).filter(node => node.tagName === 'a').map(node => node.href);
  assert.equal(exports.length, 3); assert.ok(exports.some(url => url.endsWith('kind=summary')));
});

test('returning to latest invalidates a pending session page response', async () => {
  const app = await harness(); let release;
  app.setFetch(url => url.includes('/sessions/') ? new Promise(resolve => {release = () => resolve(reply({items: [snapshot('late-session')], nextOffset: null}));}) : Promise.resolve(reply(url.endsWith('/snapshots/latest') ? snapshot('new-latest') : {items: []})));
  const pending = app.context.window.BmsWorkspace.loadSessionPage({id: 'current', name: 'late'});
  await app.click('#refresh'); release(); await pending;
  assert.equal(app.context.window.bmsViewState.snapshotId, 'new-latest'); assert.equal(app.context.window.bmsViewState.mode, 'latest');
});

test('polling preserves evidence and field groups even before native toggle events arrive', async () => {
  const source = snapshot(); const app = await harness(source);
  const catalog = {chargerVoltage: {label: '充电电压', path: 'A03.charge.ChargeVol_rx', divisor: 10, unit: 'V'}, totalVoltageAdc: {label: '电池总压', path: 'A03.State.TotalVoltage', divisor: 10, unit: 'V'}};
  app.setFetch(async url => reply(url.endsWith('/catalog') ? {items: catalog} : url.endsWith('/snapshots/latest') ? source : {items: []}));
  await app.loadProduct();
  const evidence = () => descendants(app.node('#relay-grid')).filter(node => node.tagName === 'details')[0];
  const groups = () => descendants(app.node('#catalog-tree')).filter(node => node.tagName === 'details');
  const charge = () => groups().find(node => node.textContent.includes('A03.charge'));
  evidence().open = true; charge().open = true; const oldEvidence = evidence(); const oldCharge = charge();
  await app.tick(); assert.equal(evidence().open, true); assert.equal(charge().open, true);
  oldEvidence.open = false; oldCharge.open = false; await oldEvidence.emit('toggle'); await oldCharge.emit('toggle');
  await app.tick(); assert.equal(evidence().open, true); assert.equal(charge().open, true);
  evidence().open = false; charge().open = false; await app.tick(); assert.equal(evidence().open, false); assert.equal(charge().open, false);
  app.node('#catalog-search').value = 'ChargeVol_rx'; await app.node('#catalog-search').emit('input'); assert.equal(charge().open, true);
  app.node('#catalog-search').value = ''; await app.node('#catalog-search').emit('input'); assert.equal(charge().open, false);
});

test('constant and single point trends remain centered with distinct axis labels and actual range', async () => {
  const source = snapshot(); source.packStatus = {current: {value: 3, valid: true, quality: 'valid'}};
  const app = await harness(source); app.setFetch(async url => reply(url.endsWith('/catalog') ? {items: {}} : {items: []})); await app.loadProduct();
  app.node('#trend-signal').value = 'current';
  const render = records => app.context.window.BmsProduct.render(source, {connected: true, records, index: 0, offset: 0, session: {name: 'constant'}, sourceMode: 'replay'});
  render([{snapshot: source}]);
  let circles = descendants(app.node('#trend-chart')).filter(node => node.tagName === 'circle');
  assert.equal(circles[0].attributes.cx, '402.5'); assert.equal(circles[0].attributes.cy, '125');
  const labels = descendants(app.node('#trend-chart')).filter(node => node.tagName === 'text' && node.attributes.x === '55').map(node => node.textContent);
  assert.deepEqual(labels, ['2', '3', '4']); assert.match(app.node('#trend-caption').textContent, /实际范围 3–3/);
  render([{snapshot: source}, {snapshot: {...source, timestamp: source.timestamp + 1}}]);
  circles = descendants(app.node('#trend-chart')).filter(node => node.tagName === 'circle');
  assert.equal(circles[0].attributes.cy, circles[1].attributes.cy); assert.notEqual(circles[0].attributes.cx, circles[1].attributes.cx);
});

test('manual AF can return to original automatic EF without changing server evidence', async () => {
  const source = freeze(snapshot()); const before = JSON.stringify(source); const app = await harness(source);
  await app.change('#model-override', 'af'); assert.match(app.node('#model').textContent, /AF.*manual/);
  await app.change('#model-override', ''); assert.match(app.node('#model').textContent, /EF.*automatic/);
  assert.equal(app.node('#model-reason').textContent, 'ef_evidence'); assert.equal(JSON.stringify(source), before);
});
test('replay starts at disconnection and permits next and previous with step metadata', async () => {
  const app = await harness(); const offline = snapshot('offline', 'disconnected'); const online = snapshot('online'); online.timestamp = 1002; online.current.sequence = 2;
  app.setFetch(async () => reply({items: [{snapshot: offline, timestamp: 1000, sequence: 1}, {snapshot: online, timestamp: 1002, sequence: 2}]}));
  app.node('#replay-scenario').value = 'disconnected_reconnect'; await app.click('#replay-load');
  assert.equal(app.node('#quality-state').textContent, 'DISCONNECTED'); assert.match(app.node('#replay-position')?.textContent || '', /1 \/ 2.*SEQ 1/);
  assert.equal(app.node('#replay-prev').disabled, true); await app.click('#replay-next');
  assert.equal(app.node('#quality-state').textContent, 'VALID'); assert.match(app.node('#replay-position').textContent, /2 \/ 2.*SEQ 2/);
  assert.equal(app.node('#replay-next').disabled, true); await app.click('#replay-prev'); assert.equal(app.node('#quality-state').textContent, 'DISCONNECTED');
});
test('API failure retains last number but explicitly marks it old and disconnected', async () => {
  const app = await harness(); const previousNumber = app.node('#total-voltage').textContent;
  app.setFetch(async () => {throw new Error('offline');}); await app.click('#refresh');
  assert.match(app.node('#total-voltage').textContent, /3\.8/); assert.equal(app.node('#quality-state').textContent, 'DISCONNECTED');
  assert.match(app.node('#data-status').textContent, /旧值/); assert.match(app.node('#connection-side').textContent, /失联|失败/);
  assert.ok(previousNumber);
});
test('a slower earlier snapshot response cannot replace a later user request', async () => {
  const app = await harness(); const pending = [];
  app.setFetch(url => url.endsWith('/snapshots/latest') ? new Promise(resolve => pending.push(resolve)) : Promise.resolve(reply({items: []})));
  const first = app.click('#refresh'); const second = app.click('#refresh');
  const newer = snapshot('newer'); newer.firmwareVersion = 'NEWER'; pending[1](reply(newer)); await second;
  const older = snapshot('older'); older.firmwareVersion = 'OLDER'; pending[0](reply(older)); await first;
  assert.equal(app.node('#firmware').textContent, 'NEWER');
});
test('voltage page shows software slots while a nonphysical slot is not a fault', async () => {
  const source = snapshot(); source.cells.push({...source.cells[0], cellIndex: 17, channelIndex: 17, physicalValid: false, quality: 'unknown', valid: false, value: 0});
  const app = await harness(source);
  assert.equal(app.node('#cells-page').children.length, 1); assert.equal(app.node('#quality-state').textContent, 'VALID');
  assert.equal(cellButtons(app.node('#cells-page')).length, 2);
  assert.match(cellButtons(app.node('#cells-page'))[1].textContent, /保留槽/); assert.match(app.node('#cell-summary').textContent, /1.*模组.*1.*物理.*1.*保留/);
});

test('selected cell follows replay step and suppresses trusted statistics on disconnection', async () => {
  const source = freeze(snapshot()); const app = await harness(source);
  await cellButtons(app.node('#cells-page'))[0].emit('click');
  assert.match(app.node('#cell-inspector').textContent, /模组 01.*C01/);
  assert.match(app.node('#cell-inspector').textContent, /3,800|3800/);
  const offline = snapshot('offline'); offline.connectionState = 'disconnected';
  const online = snapshot('online'); online.cells[0].value = 3900; online.cells[0].sequence = 2;
  app.setFetch(async () => reply({items: [{snapshot: offline, timestamp: 1000, sequence: 1}, {snapshot: online, timestamp: 1002, sequence: 2}]}));
  app.node('#replay-scenario').value = 'disconnected_reconnect'; await app.click('#replay-load');
  assert.match(app.node('#cell-inspector').textContent, /断线.*旧值/);
  assert.match(app.node('#cells-page').textContent, /有效压差 —/);
  assert.match(app.node('#quality-summary').textContent, /1 个物理槽无效/);
  assert.match(app.node('#total-voltage-quality').textContent, /旧值/);
  await app.click('#replay-next');
  assert.match(app.node('#cell-inspector').textContent, /3,900|3900/);
  assert.match(app.node('#cell-inspector').textContent, /序号2/);
  assert.equal(source.cells[0].value, 3800);
});

test('missing cell number cannot report a valid pack even when source quality says valid', async () => {
  const source = snapshot(); source.cells[0].value = null;
  const app = await harness(source);
  assert.equal(app.node('#quality-state').textContent, 'UNKNOWN');
  assert.match(app.node('#quality-summary').textContent, /1 个物理槽无效/);
  assert.match(app.node('#cells-page').textContent, /0\/1 有效/);
});
test('valid total voltage does not hide an invalid current or physical cell', async () => {
  const source = snapshot(); source.current.valid = false; source.current.quality = 'stale';
  const app = await harness(source); assert.notEqual(app.node('#quality-state').textContent, 'VALID'); assert.match(app.node('#current-quality').textContent, /旧值/);
});
test('empty replay response retains prior evidence and reports a failed load', async () => {
  const app = await harness(); app.setFetch(async () => reply({items: []})); app.node('#replay-scenario').value = 'valid'; await app.click('#replay-load');
  assert.match(app.node('#data-status')?.textContent || '', /失败.*旧值|旧值.*失败/); assert.match(app.node('#total-voltage').textContent, /3\.8/);
});

test('overview distinguishes ADC pack voltage, accumulated voltage and independent precharge voltage', async () => {
  const source = snapshot();
  const measurement = (value, unit, id) => ({value, unit, valid: true, quality: 'valid', source: 'simulation', timestamp: 1000, sequence: 1, rawFrameIds: [id]});
  source.packStatus = {totalVoltageAdc: measurement(512.3, 'V', 0x100), totalVoltageAccumulated: measurement(510.7, 'V', 0x100), prechargeVoltage: measurement(401.2, 'V', 0x101), current: measurement(-2.125, 'A', 0x100), soc: measurement(68.4, '%', 0x101)};
  const before = JSON.stringify(source); const app = await harness(freeze(source));
  assert.equal(app.node('#pack-voltage').textContent, '512.3 V');
  assert.equal(app.node('#total-voltage').textContent, '510.7 V');
  assert.equal(app.node('#precharge-voltage').textContent, '401.2 V');
  assert.equal(app.node('#current').textContent, '-2.125 A');
  assert.equal(app.node('#soc').textContent, '68.4 %');
  assert.match(app.node('#precharge-voltage-source').textContent, /0x101/);
  app.setFetch(async () => {throw new Error('offline');}); await app.click('#refresh');
  assert.equal(app.node('#precharge-voltage').textContent, '401.2 V');
  assert.match(app.node('#precharge-voltage-quality').textContent, /断线.*旧值/);
  assert.equal(JSON.stringify(source), before);
});

test('legacy sum never substitutes for missing ADC, precharge or SOC', async () => {
  const app = await harness();
  for (const id of ['pack-voltage', 'precharge-voltage', 'soc']) {
    assert.equal(app.node(`#${id}`).textContent, '—');
    assert.equal(app.node(`#${id}-quality`).textContent, '未接入');
  }
  assert.equal(app.node('#total-voltage').textContent, '3.8 V');
  assert.match(app.node('#total-voltage-source').textContent, /槽位计算/);
});

test('invalid or missing precharge value prevents overview from declaring every signal valid', async () => {
  const source = snapshot(); source.packStatus = {prechargeVoltage: {value: null, unit: 'V', valid: false, quality: 'partial', timestamp: 1000, source: 'simulation', rawFrameIds: [0x101]}};
  const app = await harness(source);
  assert.equal(app.node('#precharge-voltage').textContent, '—');
  assert.equal(app.node('#precharge-voltage-quality').textContent, '部分更新');
  assert.equal(app.node('#quality-state').textContent, 'PARTIAL');
});
test('analysis is disabled for replay and becomes available only after API latest reload', async () => {
  const app = await harness(); app.loadAi(); await settle(); assert.equal(app.node('#ai-analyze').disabled, false);
  app.setFetch(async url => reply(url.endsWith('/snapshots/latest') ? snapshot('latest') : url.endsWith('/ai/providers') ? {items: [{id: 'local', external: false}]} : url.endsWith('/replay/scenarios') ? {items: ['valid']} : {items: [{snapshot: snapshot('replay'), timestamp: 1000, sequence: 1}]}));
  app.node('#replay-scenario').value = 'valid'; await app.click('#replay-load');
  assert.equal(app.node('#ai-analyze').disabled, true); assert.match(app.node('#ai-target').textContent, /回放.*不支持/);
  const requestCount = app.requests.length; await app.click('#ai-analyze'); assert.equal(app.requests.length, requestCount);
  await app.click('#refresh'); assert.equal(app.node('#ai-analyze').disabled, false); assert.match(app.node('#ai-target').textContent, /latest/);
});

test('CAN and file sources use their own labels and never imply verified sampling', async () => {
  const source = snapshot('can-source', 'unknown'); source.dataSource = 'can'; source.sequenceScope = 'host_rx';
  source.frames = [{id: 0x104, timestamp: 1001, sequence: 72, bus: 'can1', direction: 'rx', dlc: 8, data: [0, 1, 2, 3, 4, 5, 6, 7]}];
  const app = await harness(source);
  assert.match(app.node('#environment-tag')?.textContent || '', /CAN/);
  assert.doesNotMatch(app.node('#data-status').textContent, /模拟/);
  assert.match(app.node('#source-note')?.textContent || '', /主机接收/);
  assert.match(app.node('#quality-detail').textContent, /有效性待确认/);
  assert.equal(app.node('#sequence').textContent, '72');
  await cellButtons(app.node('#cells-page'))[0].emit('click');
  assert.match(app.node('#cell-inspector').textContent, /主机接收序号/);
  const file = {...source, dataSource: 'file', id: 'file-source'};
  app.setFetch(async () => reply(file)); await app.click('#refresh');
  assert.match(app.node('#data-status').textContent, /离线文件/);
  assert.doesNotMatch(app.node('#data-status').textContent, /模拟/);
  assert.match(app.node('#source-note').textContent, /非实时/);
});

test('retained evidence stays inspectable without being counted as newly received frames', async () => {
  const source = snapshot('retained', 'timeout'); source.dataSource = 'can'; source.sequenceScope = 'host_rx';
  source.frames = [];
  source.evidenceFrames = [{id: 0x104, timestamp: 999, sequence: 12, bus: 'can1', direction: 'rx', dlc: 8, data: [0xd8, 0x0e, 0xd8, 0x0e, 0xd8, 0x0e, 0xd8, 0x0e]}];
  const app = await harness(freeze(source));
  assert.match(app.node('#evidence-rail').textContent, /0 个本步原始帧/);
  assert.match(app.node('#frame-table').textContent, /没有新原始帧/);
  assert.match(app.node('#evidence-frame-table')?.textContent || '', /0x104.*d8 0e/);
  assert.match(app.node('#evidence-frame-count')?.textContent || '', /1/);
});

test('automatic refresh runs at one second, avoids overlap, and respects its checkbox', async () => {
  const app = await harness();
  assert.ok([...app.intervals.values()].some(timer => timer.ms === 1000));
  const pending = [];
  app.setFetch(() => new Promise(resolve => pending.push(resolve)));
  const first = app.tick(); await app.tick(); assert.equal(pending.length, 1);
  pending[0](reply(snapshot('tick-1'))); await first;
  assert.equal(app.node('#snapshot-id').textContent, 'tick-1');
  app.node('#auto-refresh').checked = false; await app.node('#auto-refresh').emit('change');
  await app.tick(); assert.equal(pending.length, 1);
});

test('background updates preserve a scenario selection until the user loads it', async () => {
  const app = await harness(); app.node('#replay-scenario').value = 'disconnected_reconnect';
  app.setFetch(async () => reply(snapshot('updated-latest'))); await app.tick();
  assert.equal(app.node('#snapshot-id').textContent, 'updated-latest');
  assert.equal(app.node('#replay-scenario').value, 'disconnected_reconnect');
});

test('retained evidence includes every referenced frame even beyond the new-frame preview limit', async () => {
  const source = snapshot('retained');
  source.evidenceFrames = Array.from({length: 50}, (_, index) => ({id: 0x104 + index, timestamp: 999, sequence: index, bus: 'can1', direction: 'rx', dlc: 1, data: [index]}));
  const app = await harness(source);
  assert.equal(app.node('#evidence-frame-table').children.length, 50);
});

test('starting replay blocks new polling and ignores an older in-flight latest response', async () => {
  const app = await harness(); let finishLatest; let latestRequests = 0;
  app.setFetch(url => url.endsWith('/snapshots/latest') ? (latestRequests++, new Promise(resolve => {finishLatest = resolve;})) : Promise.resolve(reply({items: [{snapshot: snapshot('replay-record'), timestamp: 1000, sequence: 1}]})));
  const poll = app.tick(); await settle();
  app.node('#replay-scenario').value = 'valid'; await app.click('#replay-load');
  assert.equal(app.node('#snapshot-id').textContent, 'replay-record');
  if (finishLatest) finishLatest(reply(snapshot('stale-latest'))); await poll; await app.tick();
  assert.equal(app.node('#snapshot-id').textContent, 'replay-record'); assert.equal(latestRequests, 1);
  assert.match(app.node('#auto-refresh-note')?.textContent || '', /回放.*暂停/);
});

test('switching API ignores old source replies and restarts polling against the new base', async () => {
  const app = await harness(); let finishOld;
  app.setFetch(url => url.startsWith('http://127.0.0.1:8765') ? new Promise(resolve => {finishOld = resolve;}) : Promise.resolve(reply(url.endsWith('/snapshots/latest') ? snapshot('new-api') : {items: []})));
  const old = app.tick(); await settle();
  assert.equal(typeof finishOld, 'function', 'the first request must be in flight before the API changes');
  await app.change('#api-url', 'http://127.0.0.1:9876');
  if (finishOld) finishOld(reply(snapshot('old-api'))); await old;
  assert.equal(app.node('#snapshot-id').textContent, 'new-api');
  await app.tick(); assert.match(app.requests.at(-1)[0], /^http:\/\/127\.0\.0\.1:9876/);
});

test('an empty API response leaves an explicit null-valued offline workspace', async () => {
  const app = await harness(null);
  assert.equal(app.node('#firmware').textContent, 'unknown');
  assert.equal(cellButtons(app.node('#cells-page')).length, 144);
  assert.ok(cellButtons(app.node('#cells-page')).every(cell => /—/.test(cell.textContent)));
  assert.equal(app.node('#current').textContent, '—');
  assert.match(app.node('#data-status').textContent, /没有可显示|尚未收到/);
});
