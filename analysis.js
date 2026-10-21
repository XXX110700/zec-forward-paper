/* Explainable read-only assessment. Never creates signals or changes trades. */
function assessPosition(state, news, now = Date.now()) {
  const result = {conclusion:'暂不能形成综合结论', suggestion:'等待数据恢复后重新核对；模拟交易仍按原策略规则运行。', factors:[], risks:[], news:[]};
  if (!state || !Array.isArray(state.closed_trades)) { result.risks.push('策略状态缺失或无效'); return result; }
  const trade = state.open_trade, pending = state.pending_signal;
  const side = trade?.side || pending?.side;
  if ((trade && pending) || ((trade || pending) && !['long','short'].includes(side))) { result.risks.push('持仓或待入场信号方向无效，需核对后台状态。'); return result; }
  const scoreValid = x=>Number.isInteger(x)&&x>=0&&x<=6;
  const closeTs = state.last_processed_close_ts ?? (typeof state.last_processed_15m_ts==='number' ? state.last_processed_15m_ts+900000 : null);
  const marketFresh = typeof closeTs==='number' && now-closeTs>=-60000 && now-closeTs<=30*60000;
  const scoresValid = scoreValid(state.long_confirm)&&scoreValid(state.short_confirm);
  const technical = scoresValid ? state.long_confirm>=5&&state.long_confirm>state.short_confirm ? 'long' : state.short_confirm>=5&&state.short_confirm>state.long_confirm ? 'short' : 'neutral' : 'unknown';
  if (typeof closeTs==='number') result.factors.push(`OKX 决策 K 线收盘时间：${new Date(closeTs).toISOString()}；新闻检查时间：${news?.checked_at || '缺失'}。`);
  result.factors.push(`OKX 已收盘多周期确认：多 ${scoreValid(state.long_confirm)?state.long_confirm:'--'}/6，空 ${scoreValid(state.short_confirm)?state.short_confirm:'--'}/6；门槛 5/6。`);
  if (!marketFresh) result.risks.push('OKX 已处理 K 线超过30分钟、时间异常或缺失；不能据此判断当前行情。');
  if (!scoresValid) result.risks.push('多空确认分数无效。');
  const snapshot = state.market_snapshot;
  const finite = x=>typeof x==='number'&&Number.isFinite(x)&&x>0;
  if (snapshot && snapshot.close_ts===closeTs && finite(snapshot.close)) {
    result.factors.push(`15m收盘价 ${snapshot.close.toFixed(2)}；15m RSI ${Number.isFinite(snapshot.rsi_15m)?snapshot.rsi_15m.toFixed(1):'--'}；1h趋势分 ${snapshot.score_1h ?? '--'}、4h趋势分 ${snapshot.score_4h ?? '--'}。价格截至该 K 线收盘，非实时价格。`);
    const direction = x=>x===1?'向上':x===-1?'向下':'不足';
    result.factors.push(`1h Supertrend ${direction(snapshot.supertrend_1h)}，4h Supertrend ${direction(snapshot.supertrend_4h)}；沿用原策略的已收盘周期对齐。`);
    if (trade && finite(trade.entry) && finite(trade.risk) && ['long','short'].includes(side)) {
      const floatingR = (snapshot.close-trade.entry)/trade.risk*(side==='long'?1:-1);
      result.factors.push(`持仓按收盘价估算浮动 ${floatingR>=0?'+':''}${floatingR.toFixed(2)}R（未计平仓成本）；不等于已实现收益，也不触发止盈刷新。`);
    }
  } else result.risks.push('缺少对应 K 线的价格/指标快照，无法估算当前持仓浮动收益；需后台完成一次新 K 线处理。');
  if (trade) result.factors.push(`固定入场 ${finite(trade.entry)?trade.entry.toFixed(2):'--'}，SL ${finite(trade.sl)?trade.sl.toFixed(2):'--'}，TP1 ${finite(trade.tp1)?trade.tp1.toFixed(2):'--'}，TP2 ${finite(trade.tp2)?trade.tp2.toFixed(2):'--'}。`);
  const checked = news ? Date.parse(news.checked_at) : NaN;
  const newsFresh = Number.isFinite(checked)&&now-checked>=-60000&&now-checked<=2*60*60*1000;
  const sourcesUsable = news && news.version===1 && ['ok','partial'].includes(news.status) && Array.isArray(news.sources) && news.sources.some(s=>s.status==='ok');
  if (!newsFresh || !sourcesUsable || !Array.isArray(news?.items)) result.risks.push('新闻采集过期、失败或缺失，消息面不参与本次方向比较。');
  const ids=new Set();
  const recent = newsFresh&&sourcesUsable&&Array.isArray(news.items) ? news.items.filter(x=>{
    const t=Date.parse(x.published_at);
    if (!x.id || ids.has(x.id) || !Number.isFinite(t)||now-t<0||now-t>24*60*60*1000) return false;
    ids.add(x.id);return true;
  }) : [];
  const positive=recent.filter(x=>x.sentiment==='偏利好'), negative=recent.filter(x=>x.sentiment==='偏利空');
  const newsSide = positive.length>negative.length ? 'long' : negative.length>positive.length ? 'short' : 'neutral';
  result.factors.push(`近24小时有效消息：偏利好 ${positive.length} 条、偏利空 ${negative.length} 条、其余 ${recent.length-positive.length-negative.length} 条；仅比较标题数量，不代表事件影响大小。`);
  result.news=[...positive,...negative].sort((a,b)=>Date.parse(b.published_at)-Date.parse(a.published_at)).slice(0,4);
  if (news?.status==='partial') result.risks.push('部分新闻源采集失败，消息覆盖不完整。');
  if (recent.length) result.risks.push('消息方向来自标题关键词，未核实全文；报道可能重复或已被价格消化，不能据此预测涨跌。');
  else result.risks.push('近24小时没有可用消息，不代表不存在利好或利空。');
  if (!marketFresh || !scoresValid) return result;
  const labels={long:'多头',short:'空头',neutral:'未达单向门槛'};
  const comparison = !recent.length ? '消息面证据不足' : newsSide==='neutral' ? '消息标题方向均衡或不明确' : technical===newsSide ? '消息标题倾向与技术方向一致' : technical==='neutral' ? '消息标题有方向倾向，但技术未确认' : '消息标题倾向与技术方向冲突';
  result.conclusion=`技术${labels[technical]}；${comparison}。`;
  if (trade && ['long','short'].includes(side)) {
    const sameScore=side==='long'?state.long_confirm:state.short_confirm;
    result.suggestion=sameScore>=5 ? `模拟${labels[side]}持仓：当前同向确认仍达标，继续按已固定的 SL/TP 观察和管理。` : `模拟${labels[side]}持仓：当前同向确认已低于5/6，标记为需重点观察；评分下降本身不触发平仓。`;
    if (recent.length&&newsSide!=='neutral'&&newsSide!==side) result.suggestion+=' 消息标题方向与持仓相反，增加复核原文的优先级。';
    result.suggestion+=' 保留原止损止盈；同根触及止损和止盈仍按止损优先，不因新闻加仓、反手或提前平仓。';
  } else if (pending && ['long','short'].includes(side)) {
    result.suggestion=`已有模拟${labels[side]}待入场信号：依原规则等待下一根15m开盘处理；消息分析不取消、替换或提前执行该信号。`;
    if (recent.length&&newsSide!=='neutral'&&newsSide!==side) result.suggestion+=' 消息方向相反，入场前应复核报道和行情时效。';
  } else {
    result.suggestion=technical==='neutral' ? '空仓观察，等待原策略满足5/6及方向比较条件；不因新闻单独入场。' : `技术${labels[technical]}确认已达门槛，但当前状态没有待入场信号；核对后台处理状态，不手动补造模拟信号。`;
  }
  return result;
}
function renderAssessment() {
  const el=document.getElementById('positionAnalysis');
  if (!el) return;
  const result=assessPosition(typeof dashboardState==='undefined'?null:dashboardState,typeof newsData==='undefined'?null:newsData);
  el.replaceChildren();
  for (const [label,values] of [['综合结论',[result.conclusion]],['模拟操作建议',[result.suggestion]],['判断依据',result.factors],['风险与限制',result.risks]]) {
    const heading=document.createElement('strong');heading.textContent=label;el.append(heading);
    for(const value of values){const p=document.createElement('p');p.className='small';p.textContent=value;el.append(p);}
  }
  if(result.news.length){const h=document.createElement('strong');h.textContent='参与方向比较的消息示例';el.append(h);}
  for(const item of result.news){
    try{const u=new URL(item.url);if(!['http:','https:'].includes(u.protocol)||u.username||u.password)continue;
      const p=document.createElement('p'),a=document.createElement('a');a.href=u.href;a.target='_blank';a.rel='noopener noreferrer';a.textContent=`${item.sentiment} · ${item.source} · ${item.title}`;p.append(a);el.append(p);
    }catch(_){}
  }
}
setInterval(renderAssessment,60000);
