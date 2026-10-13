// 独立使用公开 API；不读取或保存模型密钥。
const aiButton = document.querySelector('#ai-analyze');
const aiSelect = document.querySelector('#ai-provider');
const aiStatus = document.querySelector('#ai-status');
const aiResult = document.querySelector('#ai-result');
let availableProviders = [];
let aiBusy = false;
let aiRequestVersion = 0;
let providerVersion = 0;
let aiContext = '';

function aiBase() { return document.querySelector('#api-url').value.replace(/\/$/, ''); }

function updateAiAvailability() {
  const view = window.bmsViewState || {};
  const context = `${aiBase()}|${view.mode}|${view.apiStatus}|${view.snapshotId}`;
  if (context !== aiContext) {
    aiContext = context;
    aiRequestVersion++;
    aiBusy = false;
    aiResult.replaceChildren();
    aiStatus.textContent = view.canAnalyze ? '可分析 API 最新快照；页面手动型号选择不改变分析证据。' : '请返回 API 最新快照并完成读取后再分析。';
  }
  aiButton.disabled = aiBusy || !availableProviders.length || !view.canAnalyze;
  document.querySelector('#ai-target').textContent = view.mode === 'replay' ? '正在查看回放；回放记录暂不支持分析。' : view.canAnalyze ? `分析对象：API 最新快照。当前显示 ${view.snapshotId}；分析时会读取并标明实际快照 ID。` : 'API 尚未成功读取本次快照，分析暂不可用。';
}

async function loadProviders() {
  const version = ++providerVersion;
  availableProviders = [];
  aiButton.disabled = true;
  try {
    const response = await fetch(`${aiBase()}/api/v1/ai/providers`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const providers = (await response.json()).items;
    if (version !== providerVersion) return;
    availableProviders = providers;
    aiSelect.replaceChildren(...availableProviders.map(provider => {
      const option = document.createElement('option');
      option.value = provider.id;
      option.textContent = provider.external ? `自定义模型 · ${provider.model}` : '本地规则分析（免密钥）';
      return option;
    }));
    updateAiAvailability();
  } catch {
    if (version !== providerVersion) return;
    aiStatus.textContent = '分析服务不可用，请检查 API 地址并刷新。';
  }
}

function appendText(parent, tag, content) {
  const element = document.createElement(tag);
  element.textContent = content;
  parent.append(element);
  return element;
}

aiButton.addEventListener('click', async () => {
  if (!window.bmsViewState?.canAnalyze || aiBusy) return;
  const provider = availableProviders.find(item => item.id === aiSelect.value);
  if (!provider) return;
  if (provider.external && !document.querySelector('#ai-allow-remote').checked) {
    aiStatus.textContent = '使用自定义模型前，请勾选发送本次快照的质量摘要。';
    return;
  }
  const version = ++aiRequestVersion;
  const base = aiBase();
  aiBusy = true;
  updateAiAvailability();
  aiStatus.textContent = '正在分析…';
  aiResult.replaceChildren();
  try {
    const latestResponse = await fetch(`${base}/api/v1/snapshots/latest`);
    if (!latestResponse.ok) throw new Error('无法读取快照');
    const snapshot = await latestResponse.json();
    if (version !== aiRequestVersion || !window.bmsViewState?.canAnalyze) return;
    document.querySelector('#ai-target').textContent = `本次分析对象：API 快照 ${snapshot.id} · ${new Date(snapshot.timestamp * 1000).toLocaleString()}。手动型号覆盖不参与分析。`;
    const response = await fetch(`${base}/api/v1/ai/analyze`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({provider: provider.id, snapshotId: snapshot.id, allowRemote: !!provider.external})
    });
    const report = await response.json();
    if (version !== aiRequestVersion) return;
    if (!response.ok) throw new Error(report.message || report.error || `HTTP ${response.status}`);
    appendText(aiResult, 'h3', report.summary);
    appendText(aiResult, 'p', `来源：${report.provider} / ${report.model} · 链路：${report.suggestedChain || '待定位'}`);
    appendText(aiResult, 'h4', '依据');
    const evidence = appendText(aiResult, 'ul', '');
    report.evidence.forEach(item => appendText(evidence, 'li', item));
    appendText(aiResult, 'h4', '下一步检查');
    const checks = appendText(aiResult, 'ul', '');
    report.nextChecks.forEach(item => appendText(checks, 'li', item));
    appendText(aiResult, 'small', `快照 ${report.snapshotId} · ${new Date(report.generatedAt * 1000).toLocaleString()} · ${report.ruleVersion}`);
    (report.limitations || []).forEach(item => appendText(aiResult, 'p', item));
    aiStatus.textContent = report.externalRequest ? '模型分析完成；结论需结合原始证据核对。' : '本地规则分析完成。';
  } catch (error) {
    if (version !== aiRequestVersion) return;
    aiStatus.textContent = `分析失败：${error.message}`;
  } finally { if (version === aiRequestVersion) { aiBusy = false; aiButton.disabled = !availableProviders.length || !window.bmsViewState?.canAnalyze; } }
});
document.addEventListener('bms-view-change', updateAiAvailability);
document.querySelector('#refresh').addEventListener('click', loadProviders);
document.querySelector('#api-url').addEventListener('change', loadProviders);
updateAiAvailability();
loadProviders();
