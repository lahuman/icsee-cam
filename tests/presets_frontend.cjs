const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../icsee_ptz/static/presets.js'),'utf8');
const tick=()=>new Promise(setImmediate);
function setup(role='admin'){
 const map=new Map(),calls=[],events={},windowEvents={};
 const element=id=>{if(!map.has(id))map.set(id,{value:'',textContent:'',disabled:false,children:[],events:{},setAttribute(){},addEventListener(k,f){this.events[k]=f;},replaceChildren(){this.children=[];this.value='';},append(child){this.children.push(child);if(!this.value)this.value=child.value;},set innerHTML(_){throw Error('unsafe HTML');}});return map.get(id);};
 const state={verified:false,fail:false,confirm:true,pending:null};
 vm.runInNewContext(source,{
  document:{documentElement:{dataset:{role}},hidden:false,querySelector:element,createElement:()=>({value:'',textContent:''}),addEventListener(k,f){events[k]=f;}},
  window:{confirm:()=>state.confirm,addEventListener(k,f){windowEvents[k]=f;}},AbortController,setTimeout:()=>1,clearTimeout(){},
  fetch:async(address,options={})=>{
   calls.push({address,options});
   if(state.pending&&address.endsWith('/goto'))return state.pending;
   if(state.fail)return {ok:false,status:503,json:async()=>({error:{message:'unconfirmed'}})};
   const data=address.endsWith('/stop')?{state:'stop_requested'}:{presets:[{token:'p1',name:'<img src=x onerror=alert(1)>'}],maximum:100,movement_verified:state.verified};
   return {ok:true,status:200,json:async()=>data};
  }
 });return {element,calls,state,events,windowEvents};
}
(async()=>{
 const viewer=setup('viewer');await viewer.element('#preset-refresh').events.click();assert.equal(viewer.calls.length,0);
 const t=setup();assert.equal(t.calls.length,0);assert.equal(t.element('#preset-save').disabled,true);
 await t.element('#preset-refresh').events.click();assert.equal(t.element('#preset-list').children[0].textContent,'<img src=x onerror=alert(1)>');
 assert.equal(t.element('#preset-goto').disabled,true);t.element('#preset-goto').events.click();assert.equal(t.calls.length,1);
 t.element('#preset-name').value='현관';t.element('#preset-name').events.input();await t.element('#preset-save').events.click();
 assert.deepEqual(JSON.parse(t.calls.at(-1).options.body),{name:'현관'});
 t.state.confirm=false;t.element('#preset-remove').events.click();assert.equal(t.calls.length,2);
 t.state.confirm=true;t.element('#preset-overwrite').events.click();await tick();assert.deepEqual(JSON.parse(t.calls.at(-1).options.body),{name:'현관',token:'p1'});
 t.state.fail=true;await t.element('#preset-save').events.click();assert.equal(t.element('#preset-save').disabled,true);assert.match(t.element('#preset-status').textContent,/자동 재시도하지/);
 const count=t.calls.length;await tick();assert.equal(t.calls.length,count);
 t.state.fail=false;t.state.verified=true;await t.element('#preset-refresh').events.click();assert.equal(t.element('#preset-goto').disabled,false);
 let resolve;t.state.pending=new Promise(r=>resolve=r);t.element('#preset-goto').events.click();await tick();
 assert.equal(t.element('#preset-stop').disabled,false);assert.equal(t.element('#preset-save').disabled,true);
 t.windowEvents.pagehide();await tick();assert.equal(t.calls.at(-1).address,'/api/admin/presets/stop');assert.equal(t.calls.at(-1).options.keepalive,true);
 resolve({ok:true,json:async()=>({state:'stopped',message:'도달은 영상 확인'})});await tick();await tick();
 assert.equal(t.element('#preset-stop').disabled,true);assert.match(t.element('#preset-status').textContent,/영상 확인/);
})().catch(error=>{console.error(error);process.exit(1)});
