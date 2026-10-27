const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../icsee_ptz/static/audio.js'),'utf8');
const tick=()=>new Promise(setImmediate);
function setup({secure=true,deferred=false}={}) {
 const elements=new Map(),docEvents={},winEvents={},timers=new Map(),sockets=[],tracks=[];let resolvePermission,permissions=0,nextId=0;
 function element(key){if(!elements.has(key))elements.set(key,{textContent:'',value:'2',dataset:{},attrs:{},events:{},children:{},classList:{add(){},remove(){}},setAttribute(k,v){this.attrs[k]=v},querySelector(k){return this.children[k]||(this.children[k]=element(key+' '+k))},addEventListener(k,fn){this.events[k]=fn}});return elements.get(key)}
 const node=()=>({connect(){},disconnect(){},gain:{value:1,setTargetAtTime(){}},threshold:{},knee:{},ratio:{},attack:{},release:{}});
 class Context{constructor(){this.currentTime=0;this.closed=false;this.audioWorklet={addModule:async()=>{}}}createGain(){return node()}createDynamicsCompressor(){return node()}resume(){return Promise.resolve()}close(){this.closed=true;return Promise.resolve()}createMediaStreamSource(){return node()}createBuffer(){return{getChannelData:()=>new Float32Array(320),duration:.02}}createBufferSource(){return{...node(),start(){},stop(){}}}}
 class Socket{static OPEN=1;constructor(url){this.url=url;this.readyState=1;this.bufferedAmount=0;this.sent=[];sockets.push(this)}close(){this.closed=true;this.readyState=3}send(packet){this.sent.push(packet)}}
 const stream=()=>{const track={stopped:false,stop(){this.stopped=true}};tracks.push(track);return{getTracks:()=>[track]}};
 const context={document:{hidden:false,querySelector:element,addEventListener:(k,f)=>docEvents[k]=f},window:{isSecureContext:secure,AudioWorkletNode:true,addEventListener:(k,f)=>winEvents[k]=f},navigator:{mediaDevices:{getUserMedia:()=>{permissions++;return deferred?new Promise(r=>resolvePermission=()=>r(stream())):Promise.resolve(stream())}}},location:{protocol:'https:',host:'localhost'},AudioContext:Context,AudioWorkletNode:class{constructor(){Object.assign(this,node(),{port:{}})}},WebSocket:Socket,Int16Array,Float32Array,JSON,Number,Math,Promise,Set,setTimeout:(f,delay)=>{const id=++nextId;timers.set(id,{f,delay});return id},clearTimeout:id=>timers.delete(id)};
 vm.runInNewContext(source,context);
 return {element,sockets,tracks,context,permissions:()=>permissions,resolve:()=>resolvePermission(),click:async id=>{element(id).events.click();await tick()},ready:async()=>{sockets.find(s=>s.url.endsWith('/talk')).onmessage({data:JSON.stringify({state:'ready'})});await tick()},event:(target,k,value={})=>(target==='doc'?docEvents:winEvents)[k](value),expire:()=>{const timer=[...timers.values()].find(t=>t.delay===15000);assert(timer);timer.f()},phase:id=>element(id).dataset.state};
}
(async()=>{
 let h=setup();assert.equal(h.permissions(),0);assert.equal(h.sockets.length,0);assert.equal(h.phase('#talk'),'off');
 await h.click('#talk');assert.equal(h.phase('#talk'),'connecting');await h.ready();assert.equal(h.phase('#talk'),'active');await h.click('#talk');assert.equal(h.phase('#talk'),'off');assert(h.tracks.every(t=>t.stopped));assert(h.sockets.every(s=>s.closed));
 for(const mode of ['blur','pagehide','hidden','view','escape','timeout','socket-error']){
  h=setup();await h.click('#talk');await h.ready();
  if(['blur','pagehide'].includes(mode))h.event('win',mode);
  if(mode==='hidden'){h.context.document.hidden=true;h.event('doc','visibilitychange')}
  if(mode==='view')h.event('doc','camera-view-change',{detail:{live:false}});
  if(mode==='escape')h.event('doc','keydown',{key:'Escape',preventDefault(){}});
  if(mode==='timeout')h.expire();
  if(mode==='socket-error')h.sockets[0].onerror();
  assert.equal(h.phase('#talk'),'off',mode);assert(h.tracks.every(t=>t.stopped),mode);
 }
 for(const mode of ['cancel','timeout']){
  h=setup({deferred:true});await h.click('#talk');assert.equal(h.phase('#talk'),'connecting');
  if(mode==='cancel')await h.click('#talk');else h.expire();
  h.resolve();await tick();assert(h.tracks.every(t=>t.stopped));assert.equal(h.phase('#talk'),'off');assert.equal(h.sockets.length,0);
 }
 h=setup({secure:false});await h.click('#talk');assert.equal(h.permissions(),0);assert.equal(h.phase('#talk'),'off');
 h=setup();await h.click('#listen');assert.equal(h.phase('#listen'),'connecting');
 const listen=h.sockets[0];listen.onmessage({data:new Int16Array(320).buffer});assert.equal(h.phase('#listen'),'active');
 await h.click('#talk');assert.equal(h.phase('#listen'),'paused');await h.ready();await h.click('#talk');assert.equal(h.phase('#listen'),'active');
 await h.click('#listen');assert.equal(h.phase('#listen'),'off');assert(listen.closed);
 console.log('Audio click toggle, actual ready state, timeout, late permissions, lifecycle cleanup and listening resume passed');
})().catch(e=>{console.error(e);process.exit(1)});
