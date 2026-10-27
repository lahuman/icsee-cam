(() => {
  'use strict';
  const toggle=document.querySelector('#tracking-toggle');
  const refresh=document.querySelector('#tracking-refresh');
  const status=document.querySelector('#tracking-status');
  const panel=document.querySelector('#tracking-settings');
  const overview=document.querySelector('#tracking-live-status');
  const label=document.querySelector('#tracking-live-label');
  const main=document.querySelector('#live');
  let enabled=null,busy=false,writing=false,active=false,pollTimer=null,pollBlocked=false,failed=false;

  function schedule() {
    clearTimeout(pollTimer);pollTimer=null;
    if(active&&!pollBlocked)pollTimer=setTimeout(()=>request(),30000);
  }

  function render() {
    toggle.disabled=busy||enabled===null;
    refresh.disabled=busy;
    toggle.setAttribute('aria-checked',String(enabled===true));
    toggle.dataset.state=enabled===null?'unknown':enabled?'on':'off';
    toggle.textContent=enabled===null?'확인 필요':enabled?'켜짐':'꺼짐';
    panel.setAttribute('aria-busy',String(busy));
    overview.dataset.state=writing?'unknown':enabled===null?(failed?'error':'unknown'):enabled?'on':'off';
    label.textContent=writing?'자동 추적 · 변경 확인 중':enabled===null?
      (failed?'자동 추적 · 확인 불가':'자동 추적 · 확인 중'):'자동 추적 · '+(enabled?'켜짐':'꺼짐');
  }

  async function request(value) {
    if(busy)return;
    clearTimeout(pollTimer);pollTimer=null;
    writing=typeof value==='boolean';busy=true;render();
    status.textContent=writing?'자동 추적 설정을 적용하고 확인하는 중…':'카메라의 자동 추적 상태를 확인하는 중…';
    const abort=new AbortController();
    const timer=setTimeout(()=>abort.abort(),20000);
    try {
      const response=await fetch('/api/settings/tracking',{
        method:writing?'POST':'GET',
        headers:{'Content-Type':'application/json','X-ICSEE-Control':'local'},
        body:writing?JSON.stringify({enabled:value}):undefined,
        signal:abort.signal,
      });
      const data=await response.json();
      pollBlocked=response.status===401||response.status===403||
        ['authentication','protocol'].includes(data.error?.kind);
      if(!response.ok)throw Error(response.status===401?'로그인이 만료되었습니다. 다시 로그인해 주세요.':
        data.error?.message||'카메라와 통신하지 못했습니다.');
      if(typeof data.enabled!=='boolean'||(writing&&data.enabled!==value))throw Error('카메라의 설정 값을 확인하지 못했습니다.');
      enabled=data.enabled;failed=false;
      status.textContent=(writing?'적용 확인 · ':'현재 상태 · ')+(enabled?'자동 추적이 켜져 있습니다.':'자동 추적이 꺼져 있습니다.');
    } catch(error) {
      // A lost reply may follow a successful write. Never guess or retry it.
      enabled=null;failed=true;
      status.textContent=(error.name==='AbortError'?'카메라 응답 시간이 초과됐습니다.':error.message)+
        ' 상태 새로고침으로 현재 값을 확인해 주세요.';
    } finally {
      clearTimeout(timer);busy=false;writing=false;render();schedule();
    }
  }

  toggle.addEventListener('click',()=>{if(enabled!==null&&!busy)request(!enabled);});
  refresh.addEventListener('click',()=>request());
  document.querySelector('#settings-open').addEventListener('click',()=>request());
  document.querySelector('#tracking-settings-open').addEventListener('click',()=>document.querySelector('#settings-open').click());
  function setActive(value) {
    if(active===value)return;
    active=value;clearTimeout(pollTimer);pollTimer=null;
    if(active){enabled=null;failed=false;pollBlocked=false;render();request();}
  }
  document.addEventListener('camera-view-change',event=>setActive(event.detail.live&&!document.hidden));
  document.addEventListener('visibilitychange',()=>setActive(!document.hidden&&!main.hidden));
  window.addEventListener('offline',()=>{setActive(false);enabled=null;failed=true;render();});
  window.addEventListener('online',()=>setActive(!document.hidden&&!main.hidden));
  window.addEventListener('pagehide',()=>setActive(false));
  render();
})();
