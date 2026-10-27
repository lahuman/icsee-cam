(() => {
  const listenButton=document.querySelector('#listen'),talkButton=document.querySelector('#talk');
  const status=document.querySelector('#audio-status');
  const volume=document.querySelector('#listen-volume'),volumeValue=document.querySelector('#listen-volume-value');
  const talkVolume=document.querySelector('#talk-volume'),talkVolumeValue=document.querySelector('#talk-volume-value');
  let playbackGain=2;
  let microphoneGain=1;
  function updateTalkVolume() {
    const value=Number(talkVolume.value);
    microphoneGain=Number.isFinite(value)?Math.max(0,Math.min(8,value)):1;
    talkVolumeValue.textContent=microphoneGain.toFixed(1)+'배';
    if(talking?.gain)talking.gain.gain.setTargetAtTime(microphoneGain,talking.context.currentTime,.03);
  }
  function updateVolume() {
    const value=Number(volume.value);
    playbackGain=Number.isFinite(value)?Math.max(0,Math.min(8,value)):2;
    volumeValue.textContent=playbackGain.toFixed(1)+'배';
    if(listening?.gain&&!listening.paused)listening.gain.gain.setTargetAtTime(playbackGain,listening.context.currentTime,.03);
  }
  let listening=null,talking=null,held=false,generation=0;
  const address=path=>(location.protocol==='https:'?'wss://':'ws://')+location.host+path;
  const say=text=>{if(status.textContent!==text)status.textContent=text;};
  function buttonState(button,label,description,icon,phase,pressed) {
    button.querySelector('.button-label').textContent=label;
    button.querySelector('use').setAttribute('href','#i-'+icon);
    button.dataset.state=phase;button.setAttribute('aria-pressed',String(pressed));
    button.setAttribute('aria-label',description);button.title=description;
  }
  function renderListen() {
    const phase=!listening?'off':listening.paused?'paused':listening.receiving?'active':'connecting';
    const labels={off:'듣기',paused:'일시정지',active:'듣는 중',connecting:'연결 중'};
    buttonState(listenButton,labels[phase],listening?'카메라 소리 듣기 끄기':'카메라 소리 듣기 켜기',phase==='off'?'volume-off':'volume',phase,!!listening);
  }
  function renderTalk() {
    const phase=!held?'off':talking?.node?'active':'connecting';
    buttonState(talkButton,phase==='off'?'말하기':phase==='active'?'말하는 중':'준비 중',
      held?'마이크 끄기':'마이크 말하기 켜기 · 최대 15초',held?'mic':'mic-off',phase,held);
  }
  const insecureMessage='현재 HTTP 접속에서는 마이크 권한을 요청할 수 없습니다. 휴대폰 말하기는 신뢰할 수 있는 인증서가 적용된 HTTPS 주소로 접속해야 합니다.';
  if(window.isSecureContext===false)say(insecureMessage);

  function pauseListen() {
    if(!listening)return;
    listening.paused=true;
    listening.sources.forEach(source=>{try{source.stop();}catch{}});listening.sources.clear();listening.next=0;
    listening.gain.gain.setTargetAtTime(0,listening.context.currentTime,.005);
    renderListen();
  }

  function stopListen() {
    const old=listening;listening=null;renderListen();
    listenButton.setAttribute('aria-pressed','false');
    if(old) {
      old.socket?.close();old.sources.forEach(source=>{try{source.stop();}catch{}});
      old.context?.close().catch(()=>{});
    }
  }
  async function startListen() {
    if(listening){stopListen();say('듣기를 종료했습니다.');return;}
    if(held){say('말하기를 마친 뒤 듣기를 켜 주세요.');return;}
    let context;try{context=new AudioContext();}catch{say('브라우저 오디오 재생을 시작하지 못했습니다.');return;}
    const state={context,sources:new Set(),next:0,socket:null,gain:null,compressor:null};listening=state;renderListen();
    try {
      state.gain=context.createGain();state.gain.gain.value=playbackGain;
      state.compressor=context.createDynamicsCompressor();
      state.compressor.threshold.value=-6;state.compressor.knee.value=6;state.compressor.ratio.value=12;
      state.compressor.attack.value=.003;state.compressor.release.value=.2;
      state.gain.connect(state.compressor);state.compressor.connect(context.destination);
      await context.resume();if(listening!==state){await context.close();return;}
      const socket=new WebSocket(address('/audio/listen'));state.socket=socket;socket.binaryType='arraybuffer';
      renderListen();if(!state.paused)say('주변 소리 연결 중…');
      socket.onmessage=event=>{
        if(listening!==state)return;
        if(typeof event.data==='string') {
          const message=JSON.parse(event.data);
          if(message.state==='error'){stopListen();say('주변 소리를 수신하지 못했습니다.');}
          if(message.state==='waiting'){state.receiving=false;renderListen();if(!state.paused)say('카메라 음성 연결 대기 중 · 자동 재시도합니다.');}
          return;
        }
        if(state.paused)return; // Discard during talk; never replay buffered room audio.
        const pcm=new Int16Array(event.data);
        if(!pcm.length)return;
        if(!state.receiving){state.receiving=true;renderListen();}
        if(state.next-context.currentTime>.5||state.sources.size>7) {
          state.sources.forEach(source=>{try{source.stop();}catch{}});state.sources.clear();state.next=0;
        }
        const buffer=context.createBuffer(1,pcm.length,16000),channel=buffer.getChannelData(0);
        for(let i=0;i<pcm.length;i++)channel[i]=pcm[i]/32768;
        const source=context.createBufferSource();source.buffer=buffer;source.connect(state.gain);
        source.onended=()=>state.sources.delete(source);state.sources.add(source);
        state.next=Math.max(state.next,context.currentTime+.08);source.start(state.next);state.next+=buffer.duration;
        say('주변 소리 듣는 중 · 저장하지 않음');
      };
      socket.onclose=()=>{if(listening===state){stopListen();say('주변 소리 연결이 종료됐습니다.');}};
      socket.onerror=()=>{if(listening===state){stopListen();say('주변 소리 연결에 실패했습니다.');}};
    } catch {if(listening===state)stopListen();say('브라우저 오디오 재생을 시작하지 못했습니다.');}
  }
  function stopTalk(message='말하기 종료 · 마이크 꺼짐',resumeListen=true) {
    if(!held&&!talking)return;
    held=false;generation++;talkButton.classList.remove('held');talkButton.setAttribute('aria-pressed','false');
    const old=talking;talking=null;renderTalk();
    if(old) {
      clearTimeout(old.timer);old.stream?.getTracks().forEach(track=>track.stop());
      old.node?.disconnect();old.source?.disconnect();old.gain?.disconnect();old.compressor?.disconnect();old.mute?.disconnect();
      if(old.socket?.readyState===WebSocket.OPEN)old.socket.send('stop');
      old.socket?.close();old.context?.close().catch(()=>{});
    }
    say(message);
    if(listening?.paused) {
      if(resumeListen&&!document.hidden) {
        listening.paused=false;listening.next=0;
        listening.gain.gain.setTargetAtTime(playbackGain,listening.context.currentTime,.03);
        renderListen();
      } else stopListen();
    }
  }
  async function startTalk() {
    if(held)return;
    if(window.isSecureContext===false){say(insecureMessage);return;}
    if(!navigator.mediaDevices?.getUserMedia||!window.AudioWorkletNode){say('이 브라우저는 마이크 송출을 지원하지 않습니다. 최신 Chrome 또는 Safari에서 열어 주세요.');return;}
    held=true;const id=++generation;pauseListen();
    talkButton.classList.add('held');talkButton.setAttribute('aria-pressed','true');say('마이크 권한 및 음성 연결 확인 중…');
    const state={context:null,stream:null,socket:null,node:null,source:null,gain:null,compressor:null,mute:null,timer:null};
    try {
      talking=state;renderTalk();
      state.timer=setTimeout(()=>{if(talking===state)stopTalk('15초 제한으로 종료했습니다. 다시 누르면 말할 수 있습니다.');},15000);
      state.context=new AudioContext();await state.context.resume();
      if(id!==generation||!held){state.context.close().catch(()=>{});return;}
      const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
      state.stream=stream;
      if(id!==generation||!held){stream.getTracks().forEach(track=>track.stop());return;}
      await state.context.audioWorklet.addModule('/audio-worklet.js');
      if(id!==generation||!held){stream.getTracks().forEach(track=>track.stop());return;}
      const socket=new WebSocket(address('/audio/talk'));state.socket=socket;
      socket.onmessage=event=>{
        if(talking!==state||id!==generation)return;
        const message=JSON.parse(event.data);
        if(message.state==='error'){stopTalk('카메라 음성 연결 오류 · 마이크 꺼짐');return;}
        if(message.state!=='ready'||!held||state.node)return;
        state.node=new AudioWorkletNode(state.context,'camera-microphone');state.mute=state.context.createGain();state.mute.gain.value=0;
        state.gain=state.context.createGain();state.gain.gain.value=microphoneGain;
        state.compressor=state.context.createDynamicsCompressor();
        state.compressor.threshold.value=-6;state.compressor.knee.value=6;state.compressor.ratio.value=12;
        state.compressor.attack.value=.003;state.compressor.release.value=.2;
        state.source=state.context.createMediaStreamSource(stream);state.source.connect(state.gain);
        state.gain.connect(state.compressor);state.compressor.connect(state.node);
        state.node.connect(state.mute);state.mute.connect(state.context.destination);
        state.node.port.onmessage=packet=>{
          if(talking!==state||!held||socket.readyState!==WebSocket.OPEN)return;
          if(socket.bufferedAmount>2560){stopTalk('음성 연결이 지연되어 종료했습니다.');return;}
          socket.send(packet.data);
        };
        renderTalk();
        say('카메라로 말하는 중 · 최대 15초 · 저장하지 않음');
      };
      socket.onclose=()=>{if(talking===state)stopTalk('음성 연결 종료 · 마이크 꺼짐');};
      socket.onerror=()=>{if(talking===state)stopTalk('음성 연결 실패 · 마이크 꺼짐');};
    } catch(error) {
      state.stream?.getTracks().forEach(track=>track.stop());
      if(id===generation)stopTalk(error.name==='NotAllowedError'?'마이크 권한이 허용되지 않았습니다.':'말하기를 시작하지 못했습니다. 브라우저 마이크 지원을 확인해 주세요.');
    }
  }
  renderListen();renderTalk();
  listenButton.addEventListener('click',startListen);
  volume?.addEventListener('input',updateVolume);
  talkVolume?.addEventListener('input',updateTalkVolume);
  // Native click handles touch, mouse and keyboard once, without duplicate pointer/key toggles.
  talkButton.addEventListener('click',()=>{if(held||talking)stopTalk();else startTalk();});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'&&(held||talking)){event.preventDefault();stopTalk();}});
  window.addEventListener('blur',()=>{if(held)stopTalk('말하기 종료 · 마이크 꺼짐',false);});
  document.addEventListener('visibilitychange',()=>{if(document.hidden){if(held)stopTalk('말하기 종료 · 마이크 꺼짐',false);stopListen();}});
  window.addEventListener('pagehide',()=>{if(held)stopTalk('말하기 종료 · 마이크 꺼짐',false);stopListen();});
  document.addEventListener('camera-view-change',event=>{if(!event.detail.live){if(held)stopTalk('말하기 종료 · 마이크 꺼짐',false);stopListen();}});
})();
