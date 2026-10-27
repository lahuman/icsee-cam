(() => {
  const panels={live:document.querySelector('#live'),timelapse:document.querySelector('#timelapse'),sd:document.querySelector('#sd')};
  function showView(view) {
    if(!panels[view])view='live';
    for(const [name,panel] of Object.entries(panels))panel.hidden=name!==view;
    document.dispatchEvent(new CustomEvent('camera-view-change',{detail:{live:view==='live',view}}));
    if(view!=='timelapse')document.querySelector('#archive-player').pause();
    document.querySelectorAll('nav a').forEach(link=>{
      if(link.getAttribute('href')==='#'+view)link.setAttribute('aria-current','page');
      else link.removeAttribute('aria-current');
    });
    window.scrollTo(0,0);
  }
  document.querySelectorAll('nav a').forEach(link=>link.addEventListener('click',event=>{
    event.preventDefault();const view=link.getAttribute('href').slice(1);
    history.replaceState(null,'','#'+view);showView(view);
  }));
  showView(location.hash.slice(1)||'live');
  window.addEventListener('hashchange',()=>showView(location.hash.slice(1)||'live'));
  const dates=document.querySelector('#archive-date');
  const player=document.querySelector('#archive-player'),status=document.querySelector('#archive-status');
  const build=document.querySelector('#archive-render'),download=document.querySelector('#archive-download');
  let library=null,revision=0,currentJob=null;
  const moreDates=document.querySelector('#archive-more');
  const cancelBuild=document.querySelector('#archive-cancel');
  if(cancelBuild)cancelBuild.onclick=async()=>{
    if(!currentJob)return;
    const response=await fetch('/api/jobs/'+currentJob+'/cancel',{method:'POST',headers:{'X-ICSEE-Control':'local'}});
    if(response.ok)status.textContent='영상 생성 취소 요청을 보냈습니다.';
  };
  function localTime(value) {
    return new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(value));
  }
  function choose(day) {
    revision++;
    dates.value=day;
    player.pause();player.removeAttribute('src');player.load();player.hidden=true;
    download.hidden=true;build.hidden=true;
    const item=library?.days.find(item=>item.date===day);
    if(!item){status.textContent=day+' · 저장된 타임랩스가 없습니다.';return;}
    const video=item.videos.find(video=>video.fps===30);
    status.textContent=day+' · '+item.frames+'컷 · 사진 '+(item.jpeg_bytes/1024/1024).toFixed(2)+'MB';
    if(item.last_capture)status.textContent+=' · 마지막 사진 '+localTime(item.last_capture)+' (한국 시간)';
    if(video) {
      player.src=video.url;player.hidden=false;
      download.href=video.url;download.download=day+'-30fps.mp4';download.hidden=false;
      status.textContent+=' · 재생 '+video.duration.toFixed(1)+'초';
      if(video.verified_at)status.textContent+=' · 영상 갱신 '+localTime(video.verified_at);
      if(video.stale){status.textContent+=' · 새 사진이 있어 영상 갱신이 필요합니다.';build.hidden=false;}
    } else if(item.photos_retained) {status.textContent+=' · 하루 요약 영상을 만들어 주세요.';build.hidden=false;}
    if(!item.photos_retained)status.textContent+=' · 사진 보관 종료 · 영상만 보관 중';
  }
  async function refresh(preferred,append=false) {
    const previous=append?library:null;
    const response=await fetch('/api/timelapse/days'+(append?'?offset='+library.next_offset:''));
    if(!response.ok)throw Error('저장 목록을 불러오지 못했습니다.');
    library=await response.json();
    if(previous)library.days=[...previous.days,...library.days];
    if(moreDates)moreDates.hidden=library.next_offset==null;
    library.days.sort((a,b)=>b.date.localeCompare(a.date));
    dates.replaceChildren();
    for(const item of library.days){const option=document.createElement('option');option.value=item.date;option.textContent=item.date+' · '+item.frames+'컷';dates.append(option);}
    dates.disabled=!library.days.length;
    if(!library.days.length){choose(library.yesterday);return;}
    choose(preferred||library.days[0].date);
  }
  if(moreDates)moreDates.onclick=async()=>{moreDates.disabled=true;try{await refresh(dates.value,true);}catch(error){status.textContent=error.message;}finally{moreDates.disabled=false;}};
  dates.addEventListener('change',()=>choose(dates.value));
  document.querySelector('#archive-yesterday').onclick=()=>{
    if(!library)return;
    choose(library.yesterday);
  };
  document.querySelector('#archive-refresh').onclick=()=>refresh(dates.value).catch(error=>status.textContent=error.message);
  build.onclick=async()=>{
    const day=dates.value,rate=30,token=revision;build.disabled=true;status.textContent=day+' · 영상 생성 중…';
    try {
      const response=await fetch('/api/timelapse/'+encodeURIComponent(day)+'/render',{
        method:'POST',headers:{'Content-Type':'application/json','X-ICSEE-Control':'local'},body:JSON.stringify({fps:rate})});
      if(!response.ok)throw Error('영상 생성 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.');
      if(response.status===202){
        const accepted=await response.json();currentJob=accepted.job_id;
        if(cancelBuild)cancelBuild.hidden=false;
        while(true){
          const progress=await fetch(accepted.status_url);
          if(!progress.ok)throw Error('작업 상태를 확인하지 못했습니다. 다시 로그인하거나 목록을 새로고침해 주세요.');
          const job=await progress.json();
          if(job.state==='succeeded')break;
          if(job.state==='failed'||job.state==='cancelled')throw Error(job.state==='cancelled'?'영상 생성을 취소했습니다.':'영상 생성에 실패했습니다. 원본 사진은 보존됩니다.');
          if(token===revision)status.textContent=day+(job.state==='queued'?' · 생성 순서를 기다리는 중…':' · 영상 생성·검증 중…');
          await new Promise(resolve=>setTimeout(resolve,2000));
        }
      }
      if(token===revision)await refresh(day);
    } catch(error){if(token===revision)status.textContent=error.message;}
    finally{build.disabled=false;currentJob=null;if(cancelBuild)cancelBuild.hidden=true;}
  };
  player.onerror=()=>{status.textContent+=' · 영상을 재생하지 못했습니다. 목록 새로고침 후 다시 선택해 주세요.';};
  refresh().catch(error=>status.textContent=error.message);
})();
