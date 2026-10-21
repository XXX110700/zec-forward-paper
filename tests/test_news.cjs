const fs=require('fs'),vm=require('vm'),assert=require('assert');
const elements={};
const el=id=>elements[id] ||= {textContent:'',value:'',children:[],replaceChildren(){this.children=[]},append(...nodes){this.children.push(...nodes)}};
const state={version:1,status:'partial',checked_at:new Date().toISOString(),sources:[{name:'Official',status:'error'}],items:[{title:'<img onerror=alert(1)> ZEC upgrade',url:'https://example.com/a',sentiment:'偏利好',source:'Example',published_at:new Date().toISOString(),verification:'未独立核实',basis:'标题关键词：upgrade'},{title:'unsafe',url:'javascript:alert(1)',sentiment:'偏利空'}]};
let fail=false;
const context={URL,Date,console,setInterval(){},fmtDate:d=>d?.toISOString()||'--',parseUtc:x=>x?new Date(x):null,document:{getElementById:el,createElement:()=>({children:[],append(...x){this.children.push(...x)}})},fetch:async()=>{if(fail)throw Error('offline');return {ok:true,json:async()=>state}}};
vm.createContext(context);vm.runInContext(fs.readFileSync('news.js','utf8'),context);
setImmediate(async()=>{
 assert.equal(el('newsList').children.length,1);
 assert.equal(el('newsList').children[0].children[0].textContent,state.items[0].title);
 assert.ok(el('newsStatus').textContent.includes('部分来源'));
 el('newsFilter').value='偏利空';vm.runInContext('renderNews()',context);assert.equal(el('newsList').children.length,0);
 el('newsFilter').value='';fail=true;await vm.runInContext('loadNews()',context);assert.ok(el('newsStatus').textContent.includes('可能过期'));
 state.items=[];fail=false;await vm.runInContext('loadNews()',context);assert.ok(el('newsList').textContent.includes('暂无'));
 console.log('News UI: filters, links, safe text, source failures and empty state passed');
});
