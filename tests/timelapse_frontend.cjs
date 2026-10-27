const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../icsee_ptz/static/timelapse.js'),'utf8');
const tick=()=>new Promise(setImmediate);
(async()=>{
  const elements=new Map(),calls=[];
  function element(id){
    if(!elements.has(id))elements.set(id,{value:'',textContent:'',events:{},hidden:false,
      pause(){},load(){},removeAttribute(key){delete this[key]},
      replaceChildren(){},append(){},addEventListener(key,handler){this.events[key]=handler}});
    return elements.get(id);
  }
  let version='old',stale=true,progress=0;
  const url=()=>'/api/timelapse/2026-10-26/video?fps=30&v='+version;
  vm.runInNewContext(source,{
    document:{querySelector:element,querySelectorAll:()=>[],dispatchEvent(){},createElement:()=>({})},
    window:{scrollTo(){},addEventListener(){}},location:{hash:'#timelapse'},
    CustomEvent:class {},Intl,Date,setTimeout:fn=>setImmediate(fn),history:{replaceState(){}},
    fetch:async(address,options)=>{
      calls.push({address,options});
      if(options?.method==='POST')return {ok:true,status:202,json:async()=>({job_id:'fixture',status_url:'/api/jobs/fixture'})};
      if(address==='/api/jobs/fixture'){progress++;if(progress>=3){version='new';stale=false;}return {ok:true,json:async()=>({state:progress===1?'queued':progress===2?'running':'succeeded'})};}
      return {ok:true,json:async()=>({yesterday:'2026-10-25',days:[{
        date:'2026-10-26',frames:1440,jpeg_bytes:1000,photos_retained:true,
        last_capture:'2026-10-26T02:59:35+00:00',
        videos:[{fps:30,url:url(),stale,duration:48,verified_at:'2026-10-26T02:57:23+00:00'}]
      }]})};
    }
  });
  await tick();
  const player=element('#archive-player'),download=element('#archive-download'),build=element('#archive-render');
  assert.equal(player.src,url());assert.equal(download.href,url());assert(!build.hidden);
  assert.match(element('#archive-status').textContent,/마지막 사진 11:59:35 \(한국 시간\)/);
  assert.match(element('#archive-status').textContent,/영상 갱신 11:57:23/);
  const previous=player.src;
  await build.onclick();
  assert.notEqual(player.src,previous,'regeneration changes the media resource');
  assert.equal(player.src,url());assert.equal(download.href,url());assert(build.hidden);
  assert.equal(calls.filter(call=>call.options?.method==='POST').length,1);
  assert.equal(progress,3);assert(element('#archive-cancel').hidden);
  console.log('Timelapse: regenerated playback and download, Korean capture times passed');
})().catch(error=>{console.error(error);process.exit(1)});
