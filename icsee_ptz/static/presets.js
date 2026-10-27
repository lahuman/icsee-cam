(() => {
  'use strict';
  const $=selector=>document.querySelector(selector);
  const panel=$('#preset-panel'),list=$('#preset-list'),name=$('#preset-name'),status=$('#preset-status');
  let entries=[],loaded=false,busy=false,moving=false,verified=false;
  const admin=()=>document.documentElement.dataset.role==='admin';
  function render(){
    panel.setAttribute('aria-busy',String(busy));
    $('#preset-refresh').disabled=busy;
    list.disabled=busy||!loaded||entries.length===0;
    name.disabled=busy;
    $('#preset-save').disabled=busy||!loaded||!name.value.trim();
    $('#preset-overwrite').disabled=busy||!loaded||!list.value||!name.value.trim();
    $('#preset-remove').disabled=busy||!loaded||!list.value;
    $('#preset-goto').disabled=busy||!loaded||!list.value||!verified;
    $('#preset-stop').disabled=!moving;
    $('#preset-move-note').textContent=verified?'이동은 최대 2초 후 정지합니다. 도달 여부는 영상으로 확인하세요.':'위치 이동은 실제 도달·정지 검수 후 활성화됩니다.';
  }
  function options(values){
    const previous=list.value;
    entries=values;list.replaceChildren();
    for(const item of entries){
      const option=document.createElement('option');option.value=item.token;option.textContent=item.name;list.append(option);
    }
    if(entries.some(item=>item.token===previous))list.value=previous;
  }
  async function request(action,body){
    if(busy||!admin())return;
    busy=true;moving=action==='goto';render();
    status.textContent=action==='list'?'저장 위치를 확인하는 중…':action==='goto'?'저장 위치로 이동 요청 중…':'카메라에 적용하고 결과를 확인하는 중…';
    const abort=new AbortController(),timer=setTimeout(()=>abort.abort(),20000);
    try{
      const response=await fetch('/api/admin/presets'+(action==='list'?'':'/'+action),{
        method:action==='list'?'GET':'POST',headers:{'Content-Type':'application/json','X-ICSEE-Control':'local'},
        body:body?JSON.stringify(body):undefined,signal:abort.signal});
      const data=await response.json();
      if(!response.ok)throw Error(response.status===401?'로그인이 만료됐습니다.':response.status===403?'관리자만 사용할 수 있습니다.':data.error?.message||'카메라 응답을 확인하지 못했습니다.');
      if(action==='list'){
        options(data.presets);verified=data.movement_verified===true;loaded=true;
        status.textContent=entries.length?`저장 위치 ${entries.length}개 / 최대 ${data.maximum}개`:'저장된 위치가 없습니다. 현재 위치를 저장할 수 있습니다.';
      }else if(action==='goto'){
        status.textContent=data.message||'이동을 중단했습니다. 현재 영상을 확인하세요.';
      }else{
        options(data.presets);loaded=true;
        status.textContent=action==='save'?'저장 후 카메라 목록에서 확인했습니다.':'카메라 목록에서 삭제를 확인했습니다.';
      }
    }catch(error){
      loaded=false;
      status.textContent=(error.name==='AbortError'?'응답 시간이 초과됐습니다.':error.message)+' 자동 재시도하지 않습니다. 목록 새로고침과 영상으로 결과를 확인하세요.';
    }finally{
      if(moving)stop();
      clearTimeout(timer);busy=false;moving=false;render();
    }
  }
  function stop(){
    if(!moving||!admin())return;
    fetch('/api/admin/presets/stop',{method:'POST',headers:{'Content-Type':'application/json','X-ICSEE-Control':'local'},body:'{}',keepalive:true})
      .then(response=>{if(!response.ok)status.textContent='정지 요청을 확인하지 못했습니다. 영상과 카메라 상태를 확인하세요.';})
      .catch(()=>{status.textContent='정지 요청을 확인하지 못했습니다. 영상과 카메라 상태를 확인하세요.';});
  }
  $('#preset-refresh').addEventListener('click',()=>request('list'));
  $('#preset-save').addEventListener('click',()=>request('save',{name:name.value.trim()}));
  $('#preset-overwrite').addEventListener('click',()=>{
    if(!busy&&loaded&&list.value&&window.confirm('선택한 저장 위치를 현재 카메라 위치로 덮어쓸까요?'))request('save',{name:name.value.trim(),token:list.value});
  });
  $('#preset-remove').addEventListener('click',()=>{
    if(!busy&&loaded&&list.value&&window.confirm('선택한 저장 위치를 카메라에서 삭제할까요?'))request('remove',{token:list.value});
  });
  $('#preset-goto').addEventListener('click',()=>{if(verified&&loaded&&list.value)request('goto',{token:list.value});});
  $('#preset-stop').addEventListener('click',stop);
  list.addEventListener('change',()=>{const entry=entries.find(item=>item.token===list.value);if(entry)name.value=entry.name;render();});
  name.addEventListener('input',render);
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  document.addEventListener('camera-view-change',event=>{if(!event.detail.live)stop();});
  window.addEventListener('pagehide',stop);
  render();
})();
