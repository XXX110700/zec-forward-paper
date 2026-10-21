/* Isolated news reader: never reads or writes paper-trading state. */
let newsItems = [];
function renderNews() {
  const list = document.getElementById('newsList');
  const filter = document.getElementById('newsFilter').value;
  list.replaceChildren();
  const rows = newsItems.filter(x => !filter || x.sentiment === filter);
  if (!rows.length) { list.textContent = '近7天暂无匹配消息。'; return; }
  for (const item of rows.slice(0,30)) {
    const article = document.createElement('article'); article.className = 'news-item';
    const a = document.createElement('a');
    try {
      const u = new URL(item.url);
      if (!['https:','http:'].includes(u.protocol) || u.username || u.password) continue;
      a.href = u.href;
    } catch (_) { continue; }
    a.textContent = item.title; a.target = '_blank'; a.rel = 'noopener noreferrer';
    const meta = document.createElement('div'); meta.className = 'small';
    meta.textContent = `${item.sentiment} · ${item.source} · 发布于 ${fmtDate(parseUtc(item.published_at))} · ${item.verification}`;
    const basis = document.createElement('div'); basis.className = 'small';
    basis.textContent = `${item.basis}；事件发生时间请核对原文。${item.kind === 'aggregation' ? ' 聚合链接，跳转后查看原始报道。' : ''}`;
    article.append(a,meta,basis); list.append(article);
  }
}
async function loadNews() {
  const status = document.getElementById('newsStatus');
  try {
    const response = await fetch('zec_news.json?t='+Date.now(), {cache:'no-store'});
    if (!response.ok) throw new Error('HTTP '+response.status);
    const data = await response.json();
    if (data.version !== 1 || !Array.isArray(data.items) || !Array.isArray(data.sources)) throw new Error('消息格式无效');
    newsItems = data.items;
    const checked = parseUtc(data.checked_at);
    const stale = !checked || Date.now()-checked.getTime() > 2*60*60*1000;
    status.textContent = `${data.status === 'ok' ? '采集完成' : data.status === 'partial' ? '部分来源采集失败' : '采集失败，保留历史消息'}${stale ? ' · 数据可能过期' : ''} · 检查时间：${fmtDate(checked)}`;
    document.getElementById('newsSources').textContent = data.sources.map(s=>`${s.name}：${s.status==='ok' ? '正常（'+s.matched+'条）' : '失败'}`).join(' · ');
    renderNews();
  } catch (err) {
    status.textContent = '消息读取失败，现有消息可能过期：'+err.message;
    if (!newsItems.length) document.getElementById('newsList').textContent = '暂无可用消息，请稍后重试。';
  }
}
loadNews();
setInterval(loadNews, 5*60*1000);
