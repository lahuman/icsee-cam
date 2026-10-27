const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../icsee_ptz/static/permissions.js'),'utf8');
const tick=()=>new Promise(setImmediate);
(async()=>{
  for(const role of ['viewer','admin']){
    const elements=new Map(),calls=[],root={dataset:{role:'viewer'}};
    const element=id=>{if(!elements.has(id))elements.set(id,{value:'',textContent:'',events:{},addEventListener(n,f){this.events[n]=f;}});return elements.get(id);};
    vm.runInNewContext(source,{
      document:{documentElement:root,querySelector:element},Date,location:{replace(){throw Error('unexpected redirect');}},
      fetch:async(address,options)=>{
        calls.push({address,options});
        if(address==='/api/me')return {ok:true,json:async()=>({role})};
        if(address==='/api/admin/runtime')return {ok:true,json:async()=>({viewers:3,jobs:{running:2,queued:1,failed:0},storage_bytes:1024**2,disk_free_bytes:1024**3,last_run:null})};
        if(address==='/api/admin/retention')return {ok:true,json:async()=>({photo_days:3,video_days:30})};
        throw Error('unexpected endpoint');
      }
    });
    await tick();assert.equal(root.dataset.role,role);
    await element('#settings-open').events.click();
    if(role==='viewer'){assert.equal(calls.length,1);continue;}
    assert.match(element('#runtime-status').textContent,/시청 3\/3명/);
    assert.equal(element('#retention-photos').value,3);
    element('#retention-photos').value='5';element('#retention-videos').value='40';
    await element('#retention-form').events.submit({preventDefault(){}});
    const request=calls.find(call=>call.options?.method==='POST');
    assert.deepEqual(JSON.parse(request.options.body),{photo_days:5,video_days:40});
    assert.match(element('#retention-status').textContent,/저장했습니다/);
  }
})().catch(error=>{console.error(error);process.exit(1)});
