"""Run the actual live-page script: idle views make no camera requests."""
from pathlib import Path
import shutil
import subprocess
import json
import pytest


def test_live_requests_exist_only_while_live_view_is_active():
    node=shutil.which('node')
    if not node: pytest.skip('node unavailable')
    source=(Path(__file__).resolve().parents[1]/'icsee_ptz/static/live.js').read_text()
    harness=r'''
const vm=require('node:vm'),assert=require('node:assert');
const timers=new Map(),elements=new Map(),events=new Map(),calls=[];
let nextTimer=0;
function el(selector){
  if(!elements.has(selector))elements.set(selector,{
    hidden:selector==='#live',complete:true,naturalWidth:0,value:'2',attrs:{},dataset:{},
    addEventListener(){},classList:{remove(){},add(){}},
    getAttribute(k){return this.attrs[k]??null},
    removeAttribute(k){delete this.attrs[k]},
    set src(v){this.attrs.src=v},get src(){return this.attrs.src},
  });
  return elements.get(selector);
}
const scope={
  document:{hidden:false,querySelector:el,querySelectorAll:()=>[],addEventListener:(n,f)=>events.set(n,f)},
  window:{addEventListener:(n,f)=>events.set(n,f)},
  fetch:async(url)=>{calls.push(url);return {ok:true,json:async()=>({connected:true,active:false})}},
  setTimeout:(fn,delay)=>{let id=++nextTimer;timers.set(id,{fn,delay});return id},
  clearTimeout:id=>timers.delete(id),Date,Promise,AbortController,
};
vm.runInNewContext(SOURCE,scope);
(async()=>{
  await new Promise(setImmediate);
  assert.equal(calls.length,0,'archive-only page must not check or stream the camera');
  events.get('camera-view-change')({detail:{live:true}});
  await new Promise(setImmediate);
  assert(calls.includes('/api/check'));
  const retry=[...timers.values()].find(t=>t.delay===2000);assert(retry);
  retry.fn();assert(el('#video').src.startsWith('/video?retry='));
  events.get('camera-view-change')({detail:{live:false}});
  assert.equal(el('#video').getAttribute('src'),null);
  const before=calls.length;
  for(const timer of timers.values())timer.fn();
  await new Promise(setImmediate);
  assert.equal(calls.length,before,'closed view must not resume checks');
  events.get('camera-view-change')({detail:{live:true}});
  await new Promise(setImmediate);
  scope.document.hidden=true;events.get('visibilitychange')();
  assert.equal(el('#video').getAttribute('src'),null);
  const afterHidden=calls.length;
  for(const timer of timers.values())timer.fn();
  await new Promise(setImmediate);
  assert.equal(calls.length,afterHidden);
})().catch(e=>{console.error(e);process.exit(1)});
'''
    subprocess.run([node,'-e','const SOURCE='+json.dumps(source)+';\n'+harness],
                   check=True,capture_output=True,timeout=10)
