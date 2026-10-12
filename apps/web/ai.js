// 独立使用公开 API；不读取或保存模型密钥。
const aiButton = document.querySelector('#ai-analyze');
const aiSelect = document.querySelector('#ai-provider');
const aiStatus = document.querySelector('#ai-status');
const aiResult = document.querySelector('#ai-result');
let availableProviders = [];

function aiBase() { return document.querySelector('#api-url').value.replace(/\/$/, ''); }

async function loadProviders() {
  aiButton.disabled = true;
  try {
    const response = await fetch(`${aiBase()}/api/v1/ai/providers`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    availableProviders = (await response.json()).items;
    aiSelect.replaceChildren(...availableProviders.map(provider => {
      const option = document.createElement('option');
      option.value = provider.id;
      option.textContent = provider.external ? `自定义模型 · ${provider.model}` : '本地规则分析（免密钥）';
      return option;
    }));
    aiButton.disabled = !availableProviders.length;
    aiStatus.textContent = '选择提供商后，分析最新快照。';
  } catch {
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
  const provider = availableProviders.find(item => item.id === aiSelect.value);
  if (!provider) return;
  if (provider.external && !document.querySelector('#ai-allow-remote').checked) {
    aiStatus.textContent = '使用自定义模型前，请勾选发送本次快照的质量摘要。';
    return;
  }
  aiButton.disabled = true;
  aiStatus.textContent = '正在分析…';
  aiResult.replaceChildren();
  try {
    const latestResponse = await fetch(`${aiBase()}/api/v1/snapshots/latest`);
    if (!latestResponse.ok) throw new Error('无法读取快照');
    const snapshot = await latestResponse.json();
    const response = await fetch(`${aiBase()}/api/v1/ai/analyze`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({provider: provider.id, snapshotId: snapshot.id, allowRemote: !!provider.external})
    });
    const report = await response.json();
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
    aiStatus.textContent = `分析失败：${error.message}`;
  } finally { aiButton.disabled = false; }
});
document.querySelector('#refresh').addEventListener('click', loadProviders);
document.querySelector('#api-url').addEventListener('change', loadProviders);
loadProviders();
