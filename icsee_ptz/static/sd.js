(() => {
  const date=document.querySelector('#sd-date'),hour=document.querySelector('#sd-hour');
  const status=document.querySelector('#sd-status'),list=document.querySelector('#sd-recordings');
  const options=document.querySelector('#sd-options'),start=document.querySelector('#sd-start');
  const play=document.querySelector('#sd-play'),cancel=document.querySelector('#sd-cancel');
  const more=document.querySelector('#sd-more'),player=document.querySelector('#sd-player');
  let controller=null,selected=null,cursor=null,rangeEnd=null,generation=0;
  const records=new Map();
  date.value=new Intl.DateTimeFormat('sv-SE',{timeZone:'Asia/Seoul'}).format(new Date());
  for(let h=0;h<24;h++){const option=document.createElement('option');option.value=String(h);option.textContent=String(h).padStart(2,'0')+':00 ~ '+String(h+1).padStart(2,'0')+':00';hour.append(option);}
  function stop(){generation++;controller?.abort();controller=null;cancel.hidden=true;play.disabled=false;player.pause();player.removeAttribute('src');player.load();player.hidden=true;}
  async function request(path,body){
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-ICSEE-Control':'local'},body:JSON.stringify(body),signal:controller.signal});
    const result=await response.json();if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'카메라 요청을 처리하지 못했습니다.');return result;
  }
  function range(){
    if(!/^\d{4}-\d{2}-\d{2}$/.test(date.value))throw Error('날짜를 선택해 주세요.');
    const day=new Date(date.value+'T00:00:00Z');const h=hour.value==='all'?0:Number(hour.value);
    day.setUTCHours(h);const from=day.toISOString().slice(0,19).replace('T',' ');
    day.setUTCHours(hour.value==='all'?24:h+1);
    return [from,day.toISOString().slice(0,19).replace('T',' ')];
  }
  async function search(next=false){
    stop();controller=new AbortController();const token=generation;
    try {
      const [from,to]=range();
      if(!next){records.clear();list.replaceChildren();options.hidden=true;selected=null;cursor=from;rangeEnd=to;}
      status.textContent='카메라 SD 녹화 목록 조회 중…';
      const previous=cursor,result=await request('/api/sd/search',{start:cursor,end:rangeEnd});
      if(token!==generation)return;
      for(const record of result.recordings){
        if(records.has(record.id))continue;records.set(record.id,record);
        const button=document.createElement('button');button.textContent=record.start.slice(11)+' ~ '+record.end.slice(11)+' · '+record.event;
        button.onclick=()=>{stop();selected=record;options.hidden=false;start.value=record.start.replace(' ','T');start.min=record.start.replace(' ','T');start.max=record.end.replace(' ','T');document.querySelector('#sd-selected').textContent='선택 녹화: '+record.start+' ~ '+record.end;status.textContent='시작 시각과 길이를 선택해 가져와 주세요.';};list.append(button);
      }
      cursor=result.next_start;more.hidden=!result.more||cursor===previous;
      status.textContent=records.size?'녹화 '+records.size+'개 · 구간 선택 시에만 영상을 가져옵니다.':'선택한 시간에 저장된 녹화가 없습니다.';
      if(result.more)status.textContent+=' 목록이 더 있을 수 있습니다. 다음 목록 또는 시간 범위를 좁혀 조회하세요.';
    } catch(error){if(token===generation)status.textContent=error.name==='AbortError'?'조회를 취소했습니다.':error.message;}
  }
  document.querySelector('#sd-search').onclick=()=>search(false);more.onclick=()=>search(true);
  cancel.onclick=()=>{stop();status.textContent='가져오기를 취소했습니다.';};
  play.onclick=async()=>{
    if(!selected)return;stop();controller=new AbortController();const token=generation;
    play.disabled=true;cancel.hidden=false;status.textContent='선택 구간 수신·변환 중… 최대 90초 정도 걸릴 수 있습니다.';
    try {
      const result=await request('/api/sd/preview',{recording_id:selected.id,start:(start.value.length===16?start.value+':00':start.value).replace('T',' '),seconds:Number(document.querySelector('#sd-seconds').value)});
      if(token!==generation)return;
      player.src=result.url;player.hidden=false;player.load();
      status.textContent=(result.actual_start||result.start)+' · 재생 '+result.duration.toFixed(1)+'초 · '+(result.cached?'저장된 미리보기 사용':(result.received_bytes/1024/1024).toFixed(2)+'MB 수신')+' · '+(result.audio?'음성 포함':'음성 없음');
    } catch(error){if(token===generation)status.textContent=error.name==='AbortError'?'가져오기를 취소했습니다.':error.message;}
    finally{if(token===generation){cancel.hidden=true;play.disabled=false;}}
  };
  document.addEventListener('camera-view-change',event=>{if(event.detail.view!=='sd')stop();});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stop();});
  window.addEventListener('pagehide',stop);
})();
