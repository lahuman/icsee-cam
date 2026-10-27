(() => {
  'use strict';
  const $=selector=>document.querySelector(selector);
  const main=$('#live'),video=$('#video'),message=$('#message');
  const directions=[...document.querySelectorAll('[data-dir]')];
  const pad=$('#ptz-pad');
  const write=(selector,value)=>{const node=$(selector);if(node&&node.textContent!==value)node.textContent=value;};
  let active=false,epoch=0,cameraReady=false,streamState='connecting',hasFrame=false;
  let videoTimer=null,checkTimer=null,statusTimer=null,checkAbort=null,statusAbort=null;
  let videoDelay=2000,checkDelay=2000,fallback=false,opened=0,checkBusy=false;
  let held=null,movePending=null,stopping=null,holdTimer=null,motionBusy=false,safetyFault=false;
  let inputKey=null,controlEnabled=false;
  let stopDeliveryConfirmed=false;
  const errorLabels={timeout:'장치 응답 시간 초과',authentication:'장치 인증 오류',connection:'장치 통신 오류'};

  async function request(path,data,{signal,keepalive=false,timeout=6000}={}) {
    const abort=new AbortController(),cancel=()=>abort.abort();
    signal?.addEventListener('abort',cancel,{once:true});
    if(signal?.aborted)abort.abort();
    const timer=setTimeout(cancel,timeout);
    try {
      const response=await fetch(path,{method:data===undefined?'GET':'POST',
        headers:{'Content-Type':'application/json','X-ICSEE-Control':'local'},
        body:data===undefined?undefined:JSON.stringify(data),signal:abort.signal,keepalive});
      if(!response.ok)throw Error(response.status===401?'로그인이 만료되었습니다. 다시 로그인해 주세요.':'요청을 처리하지 못했습니다. 연결을 확인해 주세요.');
      return await response.json();
    } catch(error) {
      if(error.name==='AbortError')throw Error('응답 시간 초과 · 연결을 확인해 주세요.');
      throw error;
    } finally {clearTimeout(timer);signal?.removeEventListener('abort',cancel);}
  }
  const post=(path,data={},options)=>request(path,data,options);

  // CameraPlayer: decoding stays in the browser; state sampling never replaces a healthy stream.
  function showStream(state,detail) {
    streamState=state;
    const titles={connecting:'영상 연결 중',connected:fallback?'미리보기 수신':'실시간 영상',reconnecting:'영상 재연결 중',disconnected:'영상 연결 해제',error:'영상을 불러오지 못했습니다'};
    const player=$('#player');if(player)player.dataset.state=state;
    if($('#video-overlay'))$('#video-overlay').hidden=state==='connected';
    if($('#live-badge'))$('#live-badge').hidden=state!=='connected'||fallback;
    if($('#snapshot-save'))$('#snapshot-save').disabled=state!=='connected';
    if($('#overlay-reconnect'))$('#overlay-reconnect').hidden=!['error','disconnected'].includes(state);
    write('#stream-title',titles[state]);
    if(detail)write('#stream-detail',detail);
    write('#settings-stream',titles[state]+(fallback?' · JPEG':' · MJPEG'));
  }
  function frameReceived() {
    if(!active||!video.naturalWidth||!video.getAttribute('src'))return;
    hasFrame=true;videoDelay=fallback?5000:2000;
    showStream('connected');
    const dimensions=video.naturalWidth+' × '+video.naturalHeight;
    write('#resolution',dimensions);write('#settings-resolution',dimensions);
  }
  function openVideo() {
    if(!active||!cameraReady)return;
    hasFrame=false;opened=Date.now();
    if(['error','disconnected'].includes(streamState))showStream('reconnecting','영상 수신을 다시 시도하고 있습니다.');
    video.src=(fallback?'/snapshot?retry=':'/video?retry=')+Date.now();
  }
  function retryVideo() {
    if(!active||videoTimer)return;
    videoTimer=setTimeout(()=>{
      videoTimer=null;if(!active)return;
      if(cameraReady)openVideo();else retryVideo();
    },videoDelay);
  }
  video.onload=()=>{frameReceived();if(fallback)retryVideo();};
  video.onerror=()=>{
    if(!active)return;
    hasFrame=false;showStream('error','영상 수신에 실패했습니다. 연결이 복구되면 자동으로 다시 시도합니다.');
    videoDelay=Math.min(videoDelay*2,30000);retryVideo();
  };

  // PTZController: one move per press, bounded on both client and server.
  let cameraDirections={up:'down',down:'up',left:'right',right:'left'};
  function updateControls() {
    directions.forEach(button=>button.disabled=!active||!cameraReady||!controlEnabled||safetyFault||(button!==held&&(motionBusy||!!stopping)));
    if($('#speed'))$('#speed').disabled=!controlEnabled||!!held||!!stopping||motionBusy;
    if($('#stop'))$('#stop').disabled=!controlEnabled;
  }
  function clearHold() {
    const previous=held;held=null;inputKey=null;clearTimeout(holdTimer);holdTimer=null;
    directions.forEach(button=>{button.classList.remove('held');button.setAttribute('aria-pressed','false');});
    return previous;
  }
  async function sendStop() {
    try {await post('/api/stop',{}, {keepalive:true});return true;}
    catch {
      // Only STOP is retried, once. Never retry a movement command.
      try {await post('/api/stop',{}, {keepalive:true});return true;}
      catch {return false;}
    }
  }
  function release(force=false) {
    const wasHeld=clearHold();
    if(stopping)return stopping;
    if(!controlEnabled||(!wasHeld&&!force))return Promise.resolve();
    write('#motion','정지 요청 중');
    const pending=movePending;
    stopping=(async()=>{
      // Send immediately, including while the movement HTTP request is in flight.
      let confirmed=await sendStop();
      if(pending){await pending.catch(()=>{});confirmed=await sendStop();}
      stopDeliveryConfirmed=confirmed;
      if(!confirmed){
        safetyFault=true;message.textContent='STOP 전달을 확인하지 못했습니다. 이동을 잠갔습니다. 장치 연결을 확인하고 정지 버튼을 다시 눌러 주세요.';
        write('#motion','정지 확인 필요');
      }else{write('#motion','정지 요청 전달 · 장치 응답 확인 중');}
    })().finally(()=>{stopping=null;updateControls();});
    updateControls();return stopping;
  }
  function begin(button) {
    if(held||movePending||stopping||motionBusy||safetyFault||!active||!cameraReady||!controlEnabled)return;
    held=button;button.classList.add('held');button.setAttribute('aria-pressed','true');
    stopDeliveryConfirmed=false;
    write('#motion','이동 요청 중');
    const id=epoch;
    movePending=post('/api/move',{direction:cameraDirections[button.dataset.dir],speed:Number($('#speed').value)/8})
      .catch(error=>{message.textContent=error.message;release(true);})
      .finally(()=>{movePending=null;if(id===epoch)updateControls();});
    holdTimer=setTimeout(()=>{release();message.textContent='2초 이동 제한에 도달했습니다. 다시 눌러 이동하세요.';},2000);
  }
  directions.forEach(button=>{
    button.addEventListener('pointerdown',event=>{
      if(event.button!==0||held||button.disabled)return;
      event.preventDefault();button.focus({preventScroll:true});button.setPointerCapture(event.pointerId);begin(button);
    });
    ['pointerup','pointercancel','lostpointercapture'].forEach(name=>button.addEventListener(name,()=>release()));
  });
  const keyDirections={ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right'};
  pad?.addEventListener('keydown',event=>{
    if(event.target?.id==='speed')return;
    if(event.key==='Escape'){event.preventDefault();release(true);return;}
    const direction=keyDirections[event.key]||([' ','Enter'].includes(event.key)?event.target?.dataset?.dir:null);
    if(!direction)return;
    event.preventDefault();if(event.repeat||held)return;
    const button=directions.find(item=>item.dataset.dir===direction);
    if(button&&!button.disabled){begin(button);inputKey=event.key;}
  });
  pad?.addEventListener('keyup',event=>{if(event.key===inputKey){event.preventDefault();release();}});
  pad?.addEventListener('focusout',event=>{if(!pad.contains(event.relatedTarget))release();});
  $('#ptz-details')?.addEventListener('toggle',()=>{if(!$('#ptz-details').open)release();});
  $('#stop').onclick=()=>release(true);
  $('#speed').addEventListener('input',event=>write('#speed-value',event.target.value+' / 8'));

  // Device status stays in settings; only a received video frame enables the LIVE badge.
  function applyStatus(state) {
    const wasReady=cameraReady;
    controlEnabled=state.control_enabled===true;
    if(state.direction_map)cameraDirections=state.direction_map;
    if($('#setup-notice')){
      $('#setup-notice').hidden=controlEnabled;
      $('#setup-notice').textContent=state.setup_message||'방향 제어는 장비의 이동·정지 확인 후 활성화됩니다.';
    }
    write('#settings-control',controlEnabled?'DVRIP · 이동·정지 검증됨':'영상 확인 모드 · 방향 제어 검증 필요');
    cameraReady=state.connected===true;motionBusy=state.active===true;
    const result=state.last_result||{};
    const stopFailed=Array.isArray(result.stop_errors)&&result.stop_errors.length>0;
    const hasStopEvidence=result.stop_response==='accepted'||(Array.isArray(result.stop_acknowledged_by)&&result.stop_acknowledged_by.length>0);
    if(!motionBusy&&stopFailed&&!hasStopEvidence){
      safetyFault=true;release();message.textContent='장치의 STOP 응답을 확인하지 못했습니다. 정지 버튼으로 다시 시도하세요.';
    }
    if(!motionBusy&&hasStopEvidence&&!stopFailed&&stopDeliveryConfirmed)safetyFault=false;
    if(!motionBusy&&result.error&&stopDeliveryConfirmed){
      safetyFault=true;message.textContent='정지 처리 중 장치 오류가 발생했습니다. 연결을 확인하고 정지 버튼을 다시 눌러 주세요.';
    }
    const deviceLabel=cameraReady?'장치 연결됨':state.retry_blocked?'장치 확인 필요':state.connected===false?'장치 연결 끊김':'장치 확인 중';
    write('#settings-device',deviceLabel);
    if(!held&&!stopping)write('#motion',!controlEnabled?'이동·정지 검증 필요':safetyFault?'정지 확인 필요':motionBusy?'이동 / STOP 처리 중':cameraReady?'대기 중':'연결 확인 필요');
    const error=result.error||result.move_error;
    write('#settings-error',stopFailed?'STOP 처리 중 오류가 보고되었습니다. 장치 상태를 확인해 주세요.':error?(errorLabels[error.kind]||'장치 통신 오류 · 연결을 확인해 주세요.'):'현재 보고된 오류 없음');
    if(!cameraReady){
      if(held)release();
      if(wasReady||state.retry_blocked||state.connected===false){
        video.removeAttribute('src');hasFrame=false;
        showStream(state.retry_blocked?'error':'disconnected',state.retry_blocked?'자동 재시도를 중단했습니다. 장치 인증과 서버 설정을 확인한 뒤 다시 연결해 주세요.':'장치 연결이 끊겼습니다. 자동으로 연결을 다시 확인합니다.');
      }
    }else{
      frameReceived();
      if(!hasFrame&&!video.getAttribute('src'))retryVideo();
      if(!hasFrame&&Date.now()-opened>15000&&!fallback){
        fallback=true;videoDelay=5000;write('#video-mode','미리보기 · 5초마다 갱신');
        showStream('reconnecting','실시간 영상을 받지 못해 정지 이미지 미리보기를 시도합니다.');
        clearTimeout(videoTimer);videoTimer=null;retryVideo();
      }
    }
    updateControls();
  }
  async function check(id) {
    if(!active||id!==epoch||checkBusy)return;
    checkBusy=true;checkAbort=new AbortController();
    try {
      const state=await post('/api/check',{}, {signal:checkAbort.signal});
      if(!active||id!==epoch)return;
      applyStatus(state);if(state.retry_blocked)return;
      const delay=cameraReady?30000:checkDelay;
      checkDelay=cameraReady?2000:Math.min(checkDelay*2,30000);
      checkTimer=setTimeout(()=>check(id),delay);
    }catch(error){if(active&&id===epoch){message.textContent=error.message;checkTimer=setTimeout(()=>check(id),checkDelay);checkDelay=Math.min(checkDelay*2,30000);}}
    finally {if(id===epoch)checkBusy=false;}
  }
  async function status(id) {
    if(!active||id!==epoch)return;
    statusAbort=new AbortController();
    try {const state=await request('/api/status',undefined,{signal:statusAbort.signal});if(active&&id===epoch)applyStatus(state);}
    catch {if(active&&id===epoch){cameraReady=false;if(held)release();updateControls();showStream('error','서버에 연결할 수 없습니다. 연결을 복구하면 다시 시도합니다.');write('#settings-device','서버 응답 없음');}}
    if(active&&id===epoch)statusTimer=setTimeout(()=>status(id),1000);
  }
  function setActive(enabled) {
    if(active===enabled)return;
    active=enabled;epoch++;
    clearTimeout(videoTimer);clearTimeout(checkTimer);clearTimeout(statusTimer);
    videoTimer=checkTimer=statusTimer=null;checkAbort?.abort();statusAbort?.abort();
    checkBusy=false;
    if(!active){leaveFullscreen(false);release();video.removeAttribute('src');cameraReady=false;hasFrame=false;showStream('disconnected','실시간 화면을 열면 다시 연결합니다.');write('#settings-device','확인 일시정지 · 실시간 화면에서 갱신');updateControls();return;}
    opened=Date.now();fallback=false;hasFrame=false;videoDelay=checkDelay=2000;
    write('#video-mode','전송 · MJPEG');write('#resolution','해상도 확인 중');
    showStream('connecting','카메라 연결과 영상 수신을 확인하고 있습니다.');check(epoch);status(epoch);
  }
  function reconnect() {
    if(!active)return;
    release();clearTimeout(videoTimer);clearTimeout(checkTimer);videoTimer=checkTimer=null;
    video.removeAttribute('src');hasFrame=false;fallback=false;opened=Date.now();videoDelay=2000;
    write('#video-mode','전송 · MJPEG');showStream('reconnecting','장치 연결을 확인한 뒤 영상을 다시 요청합니다.');
    check(epoch);if(cameraReady)retryVideo();
  }
  $('#refresh').onclick=()=>{if(active&&!checkBusy){clearTimeout(checkTimer);check(epoch);}};
  $('#reconnect')?.addEventListener('click',reconnect);
  $('#overlay-reconnect')?.addEventListener('click',reconnect);
  // A fixed viewport mode also works where iOS/WebViews reject element fullscreen.
  let expanded=false,nativeEntered=false,fullscreenScroll=0;
  const nativeElement=()=>document.fullscreenElement||document.webkitFullscreenElement;
  function exitNative() {
    const exit=document.exitFullscreen||document.webkitExitFullscreen;
    try {return Promise.resolve(exit?.call(document)).catch(()=>{});}catch{return Promise.resolve();}
  }
  function leaveFullscreen(restoreFocus=true) {
    if(!expanded)return;
    expanded=false;nativeEntered=false;
    const player=$('#player');player.classList.remove('is-expanded');
    document.body.classList.remove('player-expanded');
    player.removeAttribute('role');player.removeAttribute('aria-modal');player.removeAttribute('aria-label');
    $('#fullscreen')?.setAttribute('aria-expanded','false');
    if(nativeElement()===player)exitNative();
    window.scrollTo(0,fullscreenScroll);
    if(restoreFocus)$('#fullscreen')?.focus({preventScroll:true});
  }
  function enterFullscreen() {
    if(expanded){leaveFullscreen();return;}
    release();expanded=true;fullscreenScroll=window.scrollY||0;
    const player=$('#player');player.classList.add('is-expanded');
    document.body.classList.add('player-expanded');
    player.setAttribute('role','dialog');player.setAttribute('aria-modal','true');player.setAttribute('aria-label','카메라 전체화면');
    $('#fullscreen')?.setAttribute('aria-expanded','true');
    $('#fullscreen-exit')?.focus({preventScroll:true});
    const request=document.fullscreenEnabled?player.requestFullscreen:
      document.webkitFullscreenEnabled?player.webkitRequestFullscreen:null;
    if(request){
      try {Promise.resolve(request.call(player)).then(()=>{
        if(!expanded&&nativeElement()===player)exitNative();
      }).catch(()=>{});}catch{} // Keep viewport mode when the native API is unavailable or denied.
    }
  }
  function nativeFullscreenChanged() {
    if(nativeElement()===$('#player'))nativeEntered=true;
    else if(nativeEntered)leaveFullscreen();
  }
  $('#fullscreen')?.addEventListener('click',enterFullscreen);
  $('#fullscreen-exit')?.addEventListener('click',()=>leaveFullscreen());
  document.addEventListener('fullscreenchange',nativeFullscreenChanged);
  document.addEventListener('webkitfullscreenchange',nativeFullscreenChanged);
  document.addEventListener('keydown',event=>{
    if(!expanded)return;
    if(event.key==='Escape'){event.preventDefault();leaveFullscreen();}
    if(event.key==='Tab'){
      const controls=[$('#fullscreen-exit'),$('#listen'),$('#talk')].filter(button=>button&&!button.disabled);
      const index=controls.indexOf(document.activeElement);
      event.preventDefault();const next=index<0?(event.shiftKey?controls.length-1:0):(index+(event.shiftKey?-1:1)+controls.length)%controls.length;
      controls[next]?.focus();
    }
  });
  $('#snapshot-save')?.addEventListener('click',()=>{
    if(!hasFrame||streamState!=='connected')return;
    try {
      const canvas=document.createElement('canvas');canvas.width=video.naturalWidth;canvas.height=video.naturalHeight;
      canvas.getContext('2d').drawImage(video,0,0);
      canvas.toBlob(blob=>{
        if(!blob){message.textContent='스냅샷을 저장하지 못했습니다.';return;}
        const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;
        link.download='local-camera-'+new Date().toISOString().replace(/[:.]/g,'-')+'.jpg';link.click();
        setTimeout(()=>URL.revokeObjectURL(url),1000);message.textContent='현재 수신한 영상의 스냅샷을 저장했습니다.';
      },'image/jpeg',.92);
    }catch {message.textContent='스냅샷을 저장하지 못했습니다.';}
  });
  $('#settings-open')?.addEventListener('click',()=>{release();$('#settings').showModal();});
  $('#settings-close')?.addEventListener('click',()=>$('#settings').close());
  document.addEventListener('camera-view-change',event=>setActive(event.detail.live&&!document.hidden));
  document.addEventListener('visibilitychange',()=>setActive(!document.hidden&&!main.hidden));
  window.addEventListener('blur',()=>release());
  window.addEventListener('offline',()=>{release();cameraReady=false;showStream('disconnected','브라우저 네트워크 연결이 끊겼습니다.');updateControls();});
  window.addEventListener('online',()=>{if(active)reconnect();});
  window.addEventListener('pagehide',()=>{release();setActive(false);});
})();
