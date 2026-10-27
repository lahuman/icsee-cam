(() => {
  document.querySelector('#http-note').hidden=location.protocol==='https:';
  const form=document.querySelector('#login'),error=document.querySelector('#error');
  form.addEventListener('submit',async event=>{
    event.preventDefault();const button=form.querySelector('button');button.disabled=true;error.textContent='';
    try {
      const response=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json','X-ICSEE-Control':'local'},body:JSON.stringify({username:form.username.value,password:form.password.value})});
      const result=await response.json();
      if(!response.ok){error.textContent=result.error||'로그인하지 못했습니다.';return;}
      location.replace('/');
    }catch{error.textContent='서버 연결을 확인해 주세요.';}
    finally{button.disabled=false;}
  });
})();
