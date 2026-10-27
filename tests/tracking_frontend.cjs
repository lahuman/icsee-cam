const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../icsee_ptz/static/tracking.js'),'utf8');
const tick=()=>new Promise(setImmediate);
function setup(){
  const elements=new Map(),calls=[],pending=[],events={},windowEvents={},timers=new Map();let nextTimer=0;
  function element(id){
    if(!elements.has(id))elements.set(id,{dataset:{},attrs:{},events:{},textContent:'',disabled:false,
      setAttribute(k,v){this.attrs[k]=v},addEventListener(k,f){this.events[k]=f}});
    return elements.get(id);
  }
  const document={hidden:false,querySelector:element,addEventListener:(name,handler)=>events[name]=handler};
  vm.runInNewContext(source,{document,window:{addEventListener:(name,handler)=>windowEvents[name]=handler},AbortController,
    setTimeout:(fn,delay)=>{const id=++nextTimer;timers.set(id,{fn,delay});return id;},clearTimeout:id=>timers.delete(id),
    fetch:(url,options)=>{calls.push({url,...options});return new Promise((resolve,reject)=>pending.push({resolve,reject}));}});
  return {calls,element,document,timers,click:async id=>{element(id).events.click();await tick();},
    event:async(name,value={})=>{events[name](value);await tick();},
    windowEvent:async name=>{windowEvents[name]();await tick();},
    poll:async()=>{const timer=[...timers].find(([,entry])=>entry.delay===30000);assert(timer);timers.delete(timer[0]);timer[1].fn();await tick();},
    reply:async(data,status=200)=>{pending.shift().resolve({ok:status===200,status,json:async()=>data});await tick();},
    fail:async()=>{pending.shift().reject(Error('network lost'));await tick();}};
}
(async()=>{
  const h=setup(),toggle=h.element('#tracking-toggle');
  assert.equal(h.calls.length,0);assert(toggle.disabled);
  await h.click('#settings-open');assert.equal(h.calls[0].method,'GET');assert(toggle.disabled);
  await h.reply({enabled:false});assert(!toggle.disabled);assert.equal(toggle.attrs['aria-checked'],'false');
  assert.equal(h.element('#tracking-live-label').textContent,'자동 추적 · 꺼짐');
  await h.click('#tracking-toggle');
  assert.equal(h.calls[1].method,'POST');assert.equal(h.calls[1].body,'{"enabled":true}');
  assert.equal(h.calls[1].headers['X-ICSEE-Control'],'local');
  assert(toggle.disabled);assert.equal(toggle.attrs['aria-checked'],'false','do not optimistically show enabled');
  assert.match(h.element('#tracking-live-label').textContent,/변경 확인 중/);
  await h.click('#tracking-toggle');await h.click('#tracking-refresh');assert.equal(h.calls.length,2);
  await h.reply({enabled:true});assert.equal(toggle.attrs['aria-checked'],'true');assert(!toggle.disabled);
  assert.equal(h.element('#tracking-live-label').textContent,'자동 추적 · 켜짐');
  await h.click('#tracking-toggle');assert.equal(h.calls[2].body,'{"enabled":false}');
  await h.fail();assert(toggle.disabled);assert.equal(toggle.dataset.state,'unknown');
  assert.equal(h.element('#tracking-live-status').dataset.state,'error');
  await h.click('#tracking-toggle');assert.equal(h.calls.length,3,'never retry an uncertain write');
  await h.click('#tracking-refresh');assert.equal(h.calls[3].method,'GET');
  await h.reply({enabled:false});assert(!toggle.disabled);assert.equal(toggle.attrs['aria-checked'],'false');
  await h.click('#tracking-toggle');await h.reply({enabled:false});assert(toggle.disabled,'mismatch cannot be success');
  await h.click('#settings-open');await h.reply({error:'login required'},401);
  assert(toggle.disabled);assert.match(h.element('#tracking-status').textContent,/로그인/);
  const live=setup();
  await live.event('camera-view-change',{detail:{live:true}});
  assert.equal(live.calls.length,1);assert.equal(live.calls[0].method,'GET');
  await live.reply({enabled:false});await live.poll();await live.reply({enabled:true});
  assert.equal(live.element('#tracking-live-label').textContent,'자동 추적 · 켜짐');
  await live.event('camera-view-change',{detail:{live:false}});assert.equal(live.timers.size,0);
  await live.event('camera-view-change',{detail:{live:true}});await live.reply({enabled:false});
  live.document.hidden=true;await live.event('visibilitychange');assert.equal(live.timers.size,0);
  live.document.hidden=false;await live.event('visibilitychange');await live.fail();
  assert.equal(live.element('#tracking-live-label').textContent,'자동 추적 · 확인 불가');
  await live.poll();await live.reply({enabled:true});
  await live.windowEvent('pagehide');assert.equal(live.timers.size,0);
  assert(live.calls.every(call=>call.method==='GET'),'live display never writes settings');
  const expired=setup();await expired.event('camera-view-change',{detail:{live:true}});
  await expired.reply({error:'login required'},401);assert.equal(expired.timers.size,0,'do not poll after login expires');
  console.log('Tracking: confirmed display, periodic reads, lifecycle pause, uncertain writes and login expiry passed');
})().catch(error=>{console.error(error);process.exit(1)});
