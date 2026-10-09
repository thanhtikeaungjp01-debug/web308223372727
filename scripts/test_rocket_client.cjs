/* A delayed GET must not postpone or undo a cashout POST. */
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync('templates/rocket.html','utf8').split('<script>')[1].split('</script>')[0];
const nodes=new Map();
function node(id){if(!nodes.has(id))nodes.set(id,{dataset:{userId:'42'},style:{setProperty(){}},classList:{add(){},remove(){}},textContent:'',innerHTML:'',querySelectorAll(){return[];}});return nodes.get(id);}
const handlers={}, timers=[], calls=[];let pendingRead=null, readCount=0, releasePost=null;
const initial={ok:true,round_id:'r1',phase:'RUNNING',started_at:Date.now()/1000-5,multiplier:2.1,balance:900,bet:{amount:100,status:'ACTIVE'},participants:[],history:[]};
function fetch(url,options={}){
 calls.push(url);
 if(url.endsWith('/cashout'))return new Promise(resolve=>{releasePost=()=>resolve({json:async()=>({ok:true,multiplier:2.1,payout:210,balance:1110})});});
 if(++readCount===1)return Promise.resolve({json:async()=>structuredClone(initial)});
 return new Promise(resolve=>{pendingRead=()=>resolve({json:async()=>structuredClone(initial)});});
}
vm.runInNewContext(source,{document:{getElementById:node,hidden:false,addEventListener(k,f){handlers[k]=f;}},window:{addEventListener(){}},fetch,
 AbortController,performance,Date,Number,String,JSON,Math,requestAnimationFrame:()=>1,cancelAnimationFrame(){},
 setTimeout(fn,ms){timers.push({fn,ms});return timers.length;},clearTimeout(){}});
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
 await flush();
 const poll=timers.find(t=>t.ms===650);assert.ok(poll);
 poll.fn();await flush();assert.ok(pendingRead);
 const pending=node('cashoutBtn').onclick();
 node('cashoutBtn').onclick();
 assert.equal(calls.filter(x=>x.endsWith('/cashout')).length,1,'POST immediately, once, before read finishes');
 assert.equal(node('statusText').textContent,'Confirming cashout…');
 releasePost();await pending;
 assert.match(node('betInfo').textContent,/Cash out @ 2.10x/);
 assert.equal(node('balanceValue').textContent,'$11.10');
 assert.equal(node('cashoutBtn').disabled,true);
 pendingRead();await flush();
 assert.match(node('betInfo').textContent,/Cash out @ 2.10x/,'stale ACTIVE read ignored');
 assert.equal(node('balanceValue').textContent,'$11.10','stale balance ignored');
 assert.ok(!fs.readFileSync('templates/rocket.html','utf8').includes('id="poolValue"'));
 console.log('Rocket delayed-poll cashout: passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
