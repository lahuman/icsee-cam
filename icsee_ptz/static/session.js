document.querySelector('#logout').addEventListener('click',async () => {
  const button=document.querySelector('#logout');button.disabled=true;
  document.dispatchEvent(new CustomEvent('camera-view-change',{detail:{live:false}}));
  try {
    const response=await fetch('/api/logout',{method:'POST',headers:{'X-ICSEE-Control':'local'}});
    if(response.ok||response.status===401){location.replace('/login');return;}
    throw Error('logout failed');
  } catch {button.textContent='로그아웃 재시도';button.disabled=false;}
});
