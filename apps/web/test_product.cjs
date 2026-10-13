const test = require('node:test');
const assert = require('node:assert/strict');
const {signalValue, qualityOf, feedback, temperatureGroups, trendPoints} = require('./product.js');
const {validate} = require('./profiles.js');
const value = (number, quality = 'unknown') => ({value: number, quality, valid: quality === 'valid', timestamp: 1});

test('raw signal scaling and already-engineering pack values remain distinct', () => {
  const snapshot = {signals: {chargerVoltage: value(5201)}, packStatus: {totalVoltageAdc: value(506.8)}};
  const catalog = {chargerVoltage: {unit: 'V', divisor: 10}, totalVoltageAdc: {unit: 'V', divisor: 10}};
  assert.equal(signalValue(snapshot, 'chargerVoltage', catalog).value, 520.1);
  assert.equal(signalValue(snapshot, 'totalVoltageAdc', catalog).value, 506.8);
  assert.equal(snapshot.signals.chargerVoltage.value, 5201);
  assert.equal(signalValue(snapshot, 'prechargeVoltage', catalog).value, null);
});
test('relay feedback recognizes only explicit binary values and does not infer missing state', () => {
  assert.equal(feedback(null), '未收到'); assert.equal(feedback(0), '断开 · 0'); assert.equal(feedback(1), '闭合 · 1'); assert.equal(feedback(255), '异常状态 · 255');
});
test('unknown validity, missing numbers and disconnection cannot be upgraded by UI', () => {
  assert.equal(qualityOf({value: 42, item: {quality: 'valid', valid: false}}), 'unknown');
  assert.equal(qualityOf({value: null, item: value(null, 'valid')}), 'unavailable');
  assert.equal(qualityOf({value: 42, item: value(42, 'valid')}, false), 'disconnected');
  assert.equal(qualityOf({value: 3800, item: {...value(3800, 'valid'), physicalValid: false}}), 'reserved');
});
test('temperature grouping preserves 48 firmware indices and marks simulator extensions', () => {
  const source = {dataSource: 'simulation', temperatures: Array.from({length: 72}, (_, i) => value(i))};
  const groups = temperatureGroups(source);
  assert.equal(groups.length, 9); assert.equal(groups[7].channels[5].index, 47); assert.equal(groups[7].channels[5].channel, 6);
  assert.equal(groups[8].label, '模拟扩展通道'); assert.equal(groups[8].channels[0].index, 48);
  assert.equal(temperatureGroups({temperatures: []}).flatMap(g => g.channels).length, 48);
});
test('trend preserves gaps and point quality instead of fabricating healthy samples', () => {
  const records = [value(4, 'valid'), value(null), value(6, 'timeout')].map((item, i) => ({snapshot: {timestamp: 10 + i, packStatus: {current: item}, connectionState: 'connected'}}));
  assert.deepEqual(trendPoints(records, 'current').map(p => [p.value, p.quality]), [[4, 'valid'], [null, 'unavailable'], [6, 'timeout']]);
});
test('view profile validation drops unrelated fields and rejects incompatible configuration', () => {
  const profile = {version: 1, theme: 'engineering', density: 'compact', animation: 'off', autoRefresh: false, qualityOverride: 'valid'};
  assert.deepEqual(validate(profile), {version: 1, name: 'engineering', theme: 'engineering', density: 'compact', animation: 'off', autoRefresh: false});
  assert.throws(() => validate({...profile, version: 9})); assert.throws(() => validate({...profile, theme: '<script>'}));
});
