const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const html = fs.readFileSync('index.html', 'utf8');
const script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
const elements = {};
const el = id => elements[id] ||= {style:{},textContent:'',append(){},replaceChildren(){}};
const state = JSON.parse(fs.readFileSync('tests/fixtures/state.json','utf8'));
const context = {Date,Intl,Number,Math,JSON,String,console,setInterval(){},window:{},
 document:{getElementById:el,createElement:()=>({append(){}})},
 fetch:async()=>({ok:true,json:async()=>state})};
vm.createContext(context);
vm.runInContext(script,context);
setImmediate(async()=>{
 assert.equal(el('trades').textContent,'9 笔');
 assert.equal(el('totalR').textContent,'+1.76R');
 assert.equal(el('winRate').textContent,'55.6%');
 assert.equal(el('drawdown').textContent,'2.20R');
 assert.ok(el('candleTime').textContent.includes('北京时间'));
 assert.equal(vm.runInContext('parseUtc(1792517400000).getTime()',context),1792517400000);
 assert.equal(vm.runInContext('parseUtc("2026-10-20 17:30 UTC").getTime()',context),1792517400000);
 assert.ok(el('tpRefresh').textContent.includes('不追溯历史交易'));
 state.tp_confirmation_refresh={reason:'TP2',decision_ts:1792517400000,long_confirm:3,short_confirm:5,count:2};
 await vm.runInContext('loadData()',context);
 assert.ok(el('tpRefresh').textContent.includes('TP2'));
 assert.ok(el('tpRefresh').textContent.includes('多 3/6 / 空 5/6'));
 assert.ok(el('tpRefresh').textContent.includes('累计 2 次'));
 console.log('Dashboard: statistics, UTC parsing, Beijing display passed');
});
