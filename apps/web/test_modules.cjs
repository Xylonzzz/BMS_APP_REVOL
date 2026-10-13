const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// 最小 DOM 仅承接浏览器节点接口，分组与统计执行生产实现。
class Element {
  constructor(tagName = 'div') {
    this.tagName = tagName; this.children = []; this.dataset = {}; this.attributes = {};
    this.className = ''; this.listeners = {}; this._text = '';
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(''); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ''; this.children = children; }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  click() { for (const fn of this.listeners.click || []) fn({target: this}); }
}
const context = {window: {}, document: {createElement: tag => new Element(tag)}};
vm.createContext(context);
const file = path.join(__dirname, 'modules.js');
if (fs.existsSync(file)) vm.runInContext(fs.readFileSync(file, 'utf8'), context);
const api = context.window.BmsModules || {};
const cell = (cellIndex, extra = {}) => ({cellIndex, moduleIndex: 0, channelIndex: cellIndex,
  value: 3700, unit: 'mV', valid: true, quality: 'valid', physicalValid: true,
  timestamp: 1000, sequence: 1, rawFrameIds: [0x104], ...extra});
const nodes = (root, className) => [root, ...root.children.flatMap(child => nodes(child, className))]
  .filter(node => node.className.split(' ').includes(className));

test('module renderer provides grouping, statistics and rendering functions', () => {
  for (const name of ['groupCells', 'summarize', 'render']) assert.equal(typeof api[name], 'function', `${name} is not implemented`);
});

test('explicit module identities take priority and keep channels within their module', () => {
  const cells = [cell(36, {moduleIndex: 2, channelIndex: 0}),
    cell(1, {channelIndex: 1, source: 'simulated_slave_09'}), cell(0)];
  const before = JSON.stringify(cells); const groups = api.groupCells(cells);
  assert.equal(groups.length, 2); assert.equal(groups[0].id, 'module-0');
  assert.equal(groups[0].label, '模组 01'); assert.equal(groups[1].label, '模组 03');
  assert.deepEqual(Array.from(groups[0].cells, item => item.channelIndex), [0, 1]);
  assert.equal(groups[1].cells[0], cells[0]); assert.equal(JSON.stringify(cells), before);
});

test('module count follows the supplied topology without assuming eight groups', () => {
  for (const count of [1, 3, 11]) {
    const cells = Array.from({length: count}, (_, index) => cell(index * 18, {moduleIndex: index, channelIndex: 0}));
    assert.equal(api.groupCells(cells).length, count);
  }
  assert.equal(api.groupCells([]).length, 0);
});

test('legacy source confirms a group but unknown source never creates guessed topology', () => {
  const cells = [cell(0, {moduleIndex: undefined, channelIndex: undefined, source: 'simulated_slave_02'}),
    cell(1, {moduleIndex: undefined, source: 'slave_1'}),
    cell(143, {moduleIndex: undefined, source: 'simulation'}),
    cell(50, {moduleIndex: -1, source: 'slave_00'}),
    cell(51, {moduleIndex: undefined, source: 'unknown_slave_02_extra'}),
    cell(52, {moduleIndex: undefined, source: 'slave_02_extra'})];
  const groups = api.groupCells(cells);
  assert.deepEqual(Array.from(groups, group => group.id), ['module-0', 'module-1', 'ungrouped']);
  assert.equal(groups[2].label, '未分组'); assert.equal(groups[2].cells.length, 4);
  assert.equal(cells[0].channelIndex, undefined);
});

test('statistics exclude reserved slots, PEC errors, invalid flags and missing values', () => {
  const stats = api.summarize([cell(0, {value: 3701}), cell(1, {value: 3725}),
    cell(2, {value: 8000, quality: 'pec_error', valid: false}),
    cell(3, {value: 10, valid: false}), cell(4, {value: null}),
    cell(17, {value: 0, physicalValid: false})]);
  assert.equal(stats.totalCount, 6); assert.equal(stats.physicalCount, 5);
  assert.equal(stats.validCount, 2); assert.equal(stats.invalidCount, 3); assert.equal(stats.reservedCount, 1);
  assert.equal(stats.min, 3701); assert.equal(stats.max, 3725); assert.equal(stats.delta, 24);
  assert.equal(stats.quality, 'pec_error');
});

test('disconnection removes trustworthy aggregate values while leaving source data intact', () => {
  const cells = [Object.freeze(cell(0)), Object.freeze(cell(17, {physicalValid: false, value: 0}))];
  const stats = api.summarize(cells, {apiConnected: false});
  assert.equal(stats.min, null); assert.equal(stats.max, null); assert.equal(stats.delta, null);
  assert.equal(stats.validCount, 0); assert.equal(stats.invalidCount, 1); assert.equal(stats.quality, 'disconnected');
  assert.equal(cells[0].quality, 'valid'); assert.equal(cells[0].value, 3700);
  const missing = api.summarize([cell(0, {quality: 'stale', valid: false})]);
  assert.equal(missing.min, null); assert.equal(missing.max, null); assert.equal(missing.delta, null);
});

test('render creates separate module cards and accessible cell buttons with original values', () => {
  const container = new Element(); const cells = [cell(0),
    cell(1, {value: 3812, valid: false, quality: 'pec_error'}),
    cell(17, {value: 0, physicalValid: false}), cell(18, {moduleIndex: 1, channelIndex: 0})];
  let selection; let moduleSelection;
  const groups = api.render(container, cells, {onSelect: value => {selection = value;}, onModuleSelect: value => {moduleSelection = value;}});
  assert.equal(container.children.length, 2); assert.equal(groups.length, 2);
  assert.equal(container.children[0].dataset.moduleId, 'module-0');
  const buttons = nodes(container, 'cell'); assert.equal(buttons.length, 4);
  assert.equal(buttons[0].tagName, 'button'); assert.equal(buttons[0].type, 'button');
  assert.match(buttons[0].textContent, /C01/); assert.match(buttons[0].attributes['aria-label'], /3700.*mV/);
  assert.equal(buttons[1].dataset.quality, 'pec_error'); assert.match(buttons[1].textContent, /3812.*mV/);
  assert.match(buttons[1].textContent, /PEC/); assert.equal(buttons[2].dataset.quality, 'reserved');
  buttons[1].click(); assert.equal(selection.cell, cells[1]); assert.equal(selection.module.id, 'module-0');
  nodes(container, 'module-open')[1].click(); assert.equal(moduleSelection.id, 'module-1');
});

test('compact cells retain quality and evidence in titles and disconnection retains raw values', () => {
  const container = new Element(); const source = cell(0, {source: '<script>unsafe</script>'});
  api.render(container, [source], {compact: true, apiConnected: false});
  const button = nodes(container, 'cell')[0]; assert.match(button.className, /compact/);
  assert.equal(button.dataset.quality, 'disconnected'); assert.match(button.title, /3700 mV/);
  assert.match(button.title, /旧值/); assert.match(button.title, /0x104/);
  assert.match(nodes(container, 'module-range')[0].textContent, /—/);
  api.render(container, [source]); assert.equal(container.children.length, 1);
  assert.match(nodes(container, 'cell')[0].textContent, /3700/);
});
