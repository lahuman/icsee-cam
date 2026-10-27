(() => {
  let role='viewer';
  async function refreshRuntime(){
    if(role!=='admin')return;
    const label=document.querySelector('#runtime-status');
    try{
      const response=await fetch('/api/admin/runtime');
      if(!response.ok)throw Error();
      const state=await response.json(),run=state.last_run;
      const policyResponse=await fetch('/api/admin/retention');
      if(policyResponse.ok){const policy=await policyResponse.json();document.querySelector('#retention-photos').value=policy.photo_days;document.querySelector('#retention-videos').value=policy.video_days;}
      const time=run?.last_capture?new Date(run.last_capture).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}):'기록 없음';
      label.textContent=`시청 ${state.viewers}/3명 · 작업 ${state.jobs.running}개 처리 중 / ${state.jobs.queued}개 대기 · 저장 ${(state.storage_bytes/1024/1024).toFixed(1)}MB · 남은 공간 ${(state.disk_free_bytes/1024/1024/1024).toFixed(1)}GB · 마지막 촬영 ${time} · 작업 실패 ${state.jobs.failed}건`;
    }catch{label.textContent='운영 상태를 확인하지 못했습니다.';}
  }
  fetch('/api/me').then(async response=>{
    if(response.status===401){location.replace('/login');return;}
    if(!response.ok)return;
    const user=await response.json();role=user.role;
    document.documentElement.dataset.role=role;
  }).catch(()=>{});
  document.querySelector('#retention-form')?.addEventListener('submit',async event=>{
    event.preventDefault();if(role!=='admin')return;
    const label=document.querySelector('#retention-status');
    try{
      const response=await fetch('/api/admin/retention',{method:'POST',headers:{'Content-Type':'application/json','X-ICSEE-Control':'local'},body:JSON.stringify({photo_days:Number(document.querySelector('#retention-photos').value),video_days:Number(document.querySelector('#retention-videos').value)})});
      label.textContent=response.ok?'저장했습니다. 다음 보관 정리부터 적용됩니다.':'저장하지 못했습니다. 영상 보관 기간은 사진 이상이어야 합니다.';
    }catch{label.textContent='서버 연결을 확인해 주세요.';}
  });
  document.querySelector('#runtime-refresh')?.addEventListener('click',refreshRuntime);
  document.querySelector('#settings-open')?.addEventListener('click',refreshRuntime);
})();
