const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(require('node:path').join(__dirname,'../icsee_ptz/static/live.js'),'utf8');
const tick = () => new Promise(setImmediate);
function harness() {
  const elements=new Map(), events=new Map(), windowEvents=new Map(), timers=new Map(), calls=[];
  let timerId=0, now=0, moveResolve, failStop=false, failMove=false;
  let state={connected:true,active:false,last_result:null,control_enabled:true};
  function el(key) {
    if(elements.has(key))return elements.get(key);
    const element={id:key.slice(1),dataset:{},attrs:{},handlers:{},hidden:false,disabled:false,
      naturalWidth:0,naturalHeight:0,value:'2',open:true,textContent:'',held:false,classes:new Set(),
      classList:{add(name){element.held=true;element.classes.add(name)},remove(name){element.held=false;element.classes.delete(name)},contains(name){return element.classes.has(name)}},
      addEventListener(name,fn){this.handlers[name]=fn},
      getAttribute(name){return this.attrs[name]??null},setAttribute(name,value){this.attrs[name]=value},
      removeAttribute(name){delete this.attrs[name]},
      set src(value){this.attrs.src=value;calls.push({url:'IMAGE',value})},get src(){return this.attrs.src},
      setPointerCapture(){},focus(){context.document.activeElement=this},contains(target){return dirs.includes(target)||target===this},
    };
    elements.set(key,element);return element;
  }
  const dirs=['up','down','left','right'].map(dir=>{const button=el('#'+dir);button.dataset.dir=dir;return button});
  const context={document:{hidden:false,body:el('body'),querySelector:el,querySelectorAll:()=>dirs,addEventListener:(name,fn)=>events.set(name,fn)},
    window:{scrollY:240,scrollTo(x,y){this.scrollY=y},addEventListener:(name,fn)=>windowEvents.set(name,fn)},
    fetch:async(url,options={})=>{
      calls.push({url,options});
      if(url==='/api/move'){
        if(failMove)throw Error('move unavailable');
        await new Promise((resolve,reject)=>{
          moveResolve=resolve;
          options.signal.addEventListener('abort',()=>{
            const error=Error('timeout');error.name='AbortError';reject(error);
          },{once:true});
        });
      }
      if(url==='/api/stop'&&failStop)throw Error('stop unavailable');
      return {ok:true,json:async()=>url.includes('status')||url.includes('check')?state:{state:'accepted'}};
    },setTimeout:(fn,delay)=>{const id=++timerId;timers.set(id,{fn,delay});return id},
    clearTimeout:id=>timers.delete(id),AbortController,Promise,Date:class extends Date{static now(){return now}},
  };
  vm.runInNewContext(source,context);
  const event=(target,name,props={})=>{
    const handler=typeof target==='string'?(target==='window'?windowEvents:events).get(name):target.handlers[name];
    assert(handler,'event handler exists: '+name);return handler({button:0,pointerId:1,preventDefault(){},...props});
  };
  const runTimer=delay=>{const entry=[...timers].find(([,t])=>t.delay===delay);assert(entry,'timer exists: '+delay);timers.delete(entry[0]);entry[1].fn()};
  return {el,dirs,calls,events,windowEvents,context,event,runTimer,
    count:url=>calls.filter(call=>call.url===url).length,
    async activate(){event('document','camera-view-change',{detail:{live:true}});await tick()},
    async poll(){runTimer(1000);await tick()},
    resolveMove(){assert(moveResolve);moveResolve();moveResolve=null},
    setState:value=>state={control_enabled:true,...value},setFailStop:value=>failStop=value,setFailMove:value=>failMove=value,
    setTime:value=>now=value};
}
(async()=>{
  // Unverified devices can display video, but input cannot issue motor commands.
  {
    const h=harness();h.setState({connected:true,active:false,control_enabled:false,
      setup_message:'등록 후 이동·정지를 확인하세요.',direction_map:{}});await h.activate();
    assert(h.dirs.every(button=>button.disabled));
    h.event(h.dirs[0],'pointerdown');h.el('#stop').onclick();await tick();
    assert.equal(h.count('/api/move'),0);assert.equal(h.count('/api/stop'),0);
    h.runTimer(2000);assert(h.el('#video').src.startsWith('/video?retry='));
    assert.equal(h.el('#setup-notice').hidden,false);
  }
  // Newly verified devices use their observed orientation, rather than the legacy inversion.
  {
    const h=harness();h.setState({connected:true,active:false,control_enabled:true,
      direction_map:{up:'up',down:'down',left:'left',right:'right'}});await h.activate();
    h.event(h.dirs[0],'pointerdown');
    const move=h.calls.find(call=>call.url==='/api/move');
    assert.equal(JSON.parse(move.options.body).direction,'up');h.resolveMove();await tick();
    h.event(h.dirs[0],'pointerup');await tick();
  }

  // Release must reach STOP before a delayed movement response, and again after it.
  {
    const h=harness();await h.activate();
    h.event(h.dirs[0],'pointerdown');assert.equal(h.count('/api/move'),1);
    h.event(h.dirs[1],'pointerdown');assert.equal(h.count('/api/move'),1);
    h.event(h.dirs[0],'pointerup');assert.equal(h.count('/api/stop'),1);
    h.resolveMove();await tick();assert.equal(h.count('/api/stop'),2);
    assert(h.dirs.every(button=>!button.held));
  }
  for(const trigger of ['pointercancel','lostpointercapture','blur','pagehide','visibilitychange','view-change','collapse','focusout','offline']){
    const h=harness();await h.activate();h.event(h.dirs[0],'pointerdown');h.resolveMove();await tick();
    if(['pointercancel','lostpointercapture'].includes(trigger))h.event(h.dirs[0],trigger);
    if(['blur','pagehide','offline'].includes(trigger))h.event('window',trigger);
    if(trigger==='visibilitychange'){h.context.document.hidden=true;h.event('document',trigger);}
    if(trigger==='view-change')h.event('document','camera-view-change',{detail:{live:false}});
    if(trigger==='collapse'){h.el('#ptz-details').open=false;h.event(h.el('#ptz-details'),'toggle');}
    if(trigger==='focusout')h.event(h.el('#ptz-pad'),'focusout',{relatedTarget:null});
    await tick();assert(h.count('/api/stop')>=1,trigger+' must stop');assert(h.dirs.every(button=>!button.held));
  }
  // The active button stays focusable when polling reports motion, preventing an accidental early focusout.
  {
    const h=harness();await h.activate();h.runTimer(2000);h.event(h.dirs[0],'pointerdown');h.resolveMove();await tick();
    h.setState({connected:true,active:true});await h.poll();assert.equal(h.dirs[0].disabled,false);assert(h.dirs[1].disabled);
    h.runTimer(2000);await tick();assert.equal(h.count('/api/stop'),1);
  }
  // Direction keys outside the pad never send movement commands.
  {
    const h=harness();await h.activate();h.event('document','keydown',{key:'ArrowLeft'});assert.equal(h.count('/api/move'),0);assert(!h.windowEvents.has('keydown'));
    h.event(h.el('#ptz-pad'),'keydown',{key:'ArrowLeft'});
    h.event(h.el('#ptz-pad'),'keydown',{key:'ArrowLeft',repeat:true});assert.equal(h.count('/api/move'),1);
    h.event(h.el('#ptz-pad'),'keyup',{key:'ArrowLeft'});assert.equal(h.count('/api/stop'),1);
    h.resolveMove();await tick();assert.equal(h.count('/api/stop'),2);
    h.event(h.el('#ptz-pad'),'keydown',{key:'Escape'});await tick();assert.equal(h.count('/api/stop'),3);
  }
  // Failed movement still attempts STOP. Failed STOP locks movement, even with stale successful status.
  {
    const h=harness();await h.activate();h.setFailMove(true);h.event(h.dirs[0],'pointerdown');await tick();
    assert(h.count('/api/stop')>=1);
  }
  {
    const h=harness();await h.activate();h.event(h.dirs[0],'pointerdown');h.resolveMove();await tick();h.setFailStop(true);
    h.event(h.dirs[0],'pointerup');await tick();assert.equal(h.count('/api/stop'),2);
    assert(h.dirs.every(button=>button.disabled));
    h.setState({connected:true,active:false,last_result:{stop_acknowledged_by:['finally'],stop_errors:[]}});
    await h.poll();assert(h.dirs.every(button=>button.disabled),'stale STOP must not unlock a failed delivery');
    h.setFailStop(false);h.el('#stop').onclick();await tick();await h.poll();assert(h.dirs.every(button=>!button.disabled));
  }
  // All device STOP failures are visible and block new movements.
  {
    const h=harness();await h.activate();h.event(h.dirs[0],'pointerdown');
    h.runTimer(6000);await tick();
    assert.equal(h.count('/api/move'),1,'a timed-out move must never be retried');
    assert(h.count('/api/stop')>=1,'a timed-out move must still request STOP');
    assert(h.calls.filter(call=>call.url==='/api/stop').every(call=>call.options.keepalive));
  }
  {
    const h=harness();await h.activate();h.setState({connected:true,active:false,last_result:{stop_acknowledged_by:[],stop_errors:[{source:'finally'}]}});
    await h.poll();assert(h.dirs.every(button=>button.disabled));assert(h.el('#message').textContent.includes('STOP'));
  }
  // Receiving dimensions is enough to mark MJPEG as displayed; load may not fire until stream closes.
  {
    const h=harness();await h.activate();assert.equal(h.el('#player').dataset.state,'connecting');
    h.runTimer(2000);assert(h.el('#video').src.startsWith('/video?retry='));
    h.el('#video').naturalWidth=960;h.el('#video').naturalHeight=540;await h.poll();
    assert.equal(h.el('#resolution').textContent,'960 × 540');assert.equal(h.el('#player').dataset.state,'connected');
    const before=h.count('IMAGE');await h.poll();assert.equal(h.count('IMAGE'),before,'healthy video must not restart');
    h.el('#video').onerror();assert.equal(h.el('#player').dataset.state,'error');
    h.el('#video').naturalWidth=0;h.runTimer(4000);assert.equal(h.count('IMAGE'),before+1);
    h.setState({connected:false,active:false});await h.poll();assert.equal(h.el('#player').dataset.state,'disconnected');
    assert.equal(h.el('#video').getAttribute('src'),null);
    h.setState({connected:true,active:false});await h.poll();h.runTimer(4000);assert(h.el('#video').src.startsWith('/video?retry='));
    h.setTime(20000);await h.poll();h.runTimer(5000);assert(h.el('#video').src.startsWith('/snapshot?retry='));
    h.el('#video').naturalWidth=1920;h.el('#video').naturalHeight=1080;h.el('#video').onload();
    assert.equal(h.el('#live-badge').hidden,true,'snapshot preview must never claim LIVE');
    h.event(h.el('#reconnect'),'click');assert.equal(h.el('#player').dataset.state,'reconnecting');
  }
  for(const mode of ['unsupported','rejected','native','webkit']){
    const h=harness();await h.activate();const player=h.el('#player'),doc=h.context.document;
    if(mode==='rejected'){doc.fullscreenEnabled=true;player.requestFullscreen=()=>Promise.reject(Error('unsupported'));}
    if(mode==='native'){doc.fullscreenEnabled=true;player.requestFullscreen=()=>{doc.fullscreenElement=player;h.event('document','fullscreenchange')};doc.exitFullscreen=()=>{doc.fullscreenElement=null;h.event('document','fullscreenchange')};}
    if(mode==='webkit'){doc.webkitFullscreenEnabled=true;player.webkitRequestFullscreen=()=>{doc.webkitFullscreenElement=player;h.event('document','webkitfullscreenchange')};doc.webkitExitFullscreen=()=>{doc.webkitFullscreenElement=null;h.event('document','webkitfullscreenchange')};}
    h.event(h.el('#fullscreen'),'click');await tick();
    assert(player.classList.contains('is-expanded'),mode+' enters viewport fullscreen');
    assert(doc.body.classList.contains('player-expanded'));
    assert.equal(doc.activeElement,h.el('#fullscreen-exit'));
    const moves=h.count('/api/move');h.event('document','keydown',{key:'ArrowUp'});assert.equal(h.count('/api/move'),moves);
    h.event(h.el('#fullscreen-exit'),'click');await tick();
    assert(!player.classList.contains('is-expanded'));assert(!doc.body.classList.contains('player-expanded'));
    assert.equal(h.context.window.scrollY,240);assert.equal(doc.activeElement,h.el('#fullscreen'));
    h.event(h.el('#fullscreen'),'click');await tick();
    h.event('document','keydown',{key:'Escape'});await tick();assert(!player.classList.contains('is-expanded'));
    h.event(h.el('#fullscreen'),'click');await tick();
    h.event('document','camera-view-change',{detail:{live:false}});await tick();assert(!player.classList.contains('is-expanded'));
  }
  console.log('PTZ safety, bounded commands, stop failure recovery, keyboard scope and stream recovery passed');
})().catch(error=>{console.error(error);process.exit(1)});
