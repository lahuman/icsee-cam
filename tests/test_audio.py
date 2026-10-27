import asyncio
import shutil
import struct
import subprocess
import time
from unittest.mock import Mock,patch
import pytest
from icsee_ptz.audio import DVRIPTalk,pcm16_to_alaw,PCM_PACKET_BYTES
from icsee_ptz.config import Config,ProtocolError
from icsee_ptz.dvrip import HEADER


def test_g711_matches_ffmpeg_across_all_int16_samples():
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg unavailable')
    pcm=struct.pack('<65536h',*range(-32768,32768))
    reference=subprocess.run([ffmpeg,'-loglevel','quiet','-f','s16le','-ar','8000','-ac','1',
        '-i','pipe:0','-c:a','pcm_alaw','-f','alaw','pipe:1'],input=pcm,capture_output=True,check=True,timeout=10).stdout
    assert pcm16_to_alaw(pcm)==reference


def test_alaw_silence_and_bad_alignment():
    assert pcm16_to_alaw(b'\0'*640)==b'\xd5'*320
    with pytest.raises(ProtocolError):pcm16_to_alaw(b'x')


def test_audio_packet_format_and_bounded_size():
    talk=DVRIPTalk(Config(password='test-only'))
    talk.client.sock=Mock();talk.client.session=123;talk.started=time.monotonic()
    talk.send_pcm(b'\0'*640)
    packet=talk.client.sock.sendall.call_args.args[0]
    assert HEADER.unpack(packet[:20])[4:]==(1432,328)
    assert packet[20:28]==b'\0\0\x01\xfa\x0e\x02\x40\x01'
    assert packet[28:]==b'\xd5'*320
    with pytest.raises(ProtocolError):talk.send_pcm(b'\0'*1280)
    talk.close()


def test_fast_audio_flood_is_rejected():
    talk=DVRIPTalk(Config(password='test-only'))
    talk.client.sock=Mock();talk.started=time.monotonic();talk.packets=10
    with pytest.raises(ProtocolError):talk.send_pcm(b'\0'*640)
    talk.close()


def test_idle_watchdog_closes_blocked_talk_independently():
    talk=DVRIPTalk(Config(password='test-only'))
    sock=Mock();talk.client.sock=sock;talk.last_input=time.monotonic()-2
    talk._watch_inactivity()
    assert talk.closed.is_set()
    sock.shutdown.assert_called_once()
    sock.close.assert_called_once()


def test_open_rejection_closes_connection_without_start():
    talk=DVRIPTalk(Config(password='test-only'))
    with patch.object(talk.client,'__enter__'),patch.object(talk.client,'_exchange',return_value={'Ret':103}),patch.object(talk,'_write') as write:
        with pytest.raises(ProtocolError):talk.open()
        write.assert_not_called()
    assert talk.closed.is_set()


def test_worklet_resamples_and_emits_40ms_pcm_without_loopback():
    node=shutil.which('node')
    if not node:pytest.skip('node unavailable')
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'icsee_ptz/static/audio-worklet.js'
    program='''let Processor;let buffers=[];
      global.sampleRate=48000;
      global.AudioWorkletProcessor=class {constructor(){this.port={postMessage:b=>buffers.push(b)}}};
      global.registerProcessor=(name,p)=>Processor=p;
      require(process.argv[1]);const processor=new Processor();
      for(let i=0;i<375;i++)processor.process([[new Float32Array(128).fill(.5)]]);
      if(buffers.length!==25||buffers.some(b=>b.byteLength!==640))process.exit(1);
      if(new Int16Array(buffers[0])[0]!==16384)process.exit(2);
      console.log('1 second -> 25 PCM packets');'''
    result=subprocess.run([node,'-e',program,str(path)],capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr.decode()

@pytest.mark.parametrize('mode',['cancel','deny','insecure'])
def test_microphone_permission_race_cannot_leave_live_track_or_open_socket(mode):
    node=shutil.which('node')
    if not node:pytest.skip('node unavailable')
    from pathlib import Path
    script=Path(__file__).resolve().parents[1]/'icsee_ptz/static/audio.js'
    program=r'''
      let resolveMic,rejectMic,stops=0,sockets=0,closed=0;
      const elements={};
      for(const id of ['#listen','#talk','#audio-status'])elements[id]={
        events:{},dataset:{},querySelector(){return this;},textContent:'',classList:{add(){},remove(){}},
        setAttribute(){},setPointerCapture(){},addEventListener(n,f){this.events[n]=f;}};
      global.document={hidden:false,querySelector:id=>elements[id],addEventListener(){}};
      global.window={isSecureContext:process.argv[2]!=='insecure',AudioWorkletNode:class{},addEventListener(){}};
      global.AudioContext=class {
        constructor(){this.audioWorklet={addModule:async()=>{throw Error('must not load after cancellation')}};}
        resume(){return Promise.resolve();}close(){closed++;return Promise.resolve();}};
      global.WebSocket=class {constructor(){sockets++;}static OPEN=1;};
      Object.defineProperty(globalThis,'navigator',{value:{mediaDevices:{getUserMedia:()=>new Promise((yes,no)=>{resolveMic=yes;rejectMic=no;})}}});
      global.location={protocol:'http:',host:'127.0.0.1:8000'};
      require(process.argv[1]);
      (async()=>{
        elements['#talk'].events.click();
        await new Promise(resolve=>setImmediate(resolve));
        if(process.argv[2]==='insecure'){
          elements['#talk'].events.click();
          if(resolveMic||sockets||!elements['#audio-status'].textContent.includes('HTTPS'))throw Error('insecure context not explained safely');
          return;
        }
        if(!resolveMic)throw Error('permission request did not start');
        if(process.argv[2]==='cancel'){
          elements['#talk'].events.click();
          resolveMic({getTracks:()=>[{stop(){stops++;}}]});
        }else rejectMic(Object.assign(Error('denied'),{name:'NotAllowedError'}));
        await new Promise(resolve=>setImmediate(resolve));
        if(process.argv[2]==='deny'){
          if(!elements['#audio-status'].textContent.includes('권한'))throw Error('permission error erased on release');
        }
        if(sockets!==0||closed<1)throw Error('audio resources leaked');
        if(process.argv[2]==='cancel'&&stops!==1)throw Error('late microphone track stayed active');
        console.log('microphone cancellation safe');
      })().catch(error=>{console.error(error.message);process.exitCode=1;});
    '''
    result=subprocess.run([node,'-e',program,str(script),mode],capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr.decode()


def test_listen_volume_changes_active_playback_without_modifying_pcm_or_reopening_socket():
    node=shutil.which('node')
    if not node:pytest.skip('node unavailable')
    from pathlib import Path
    script=Path(__file__).resolve().parents[1]/'icsee_ptz/static/audio.js'
    program=r'''
      const elements={},sockets=[],contexts=[];
      for(const id of ['#listen','#talk','#audio-status','#listen-volume','#listen-volume-value'])elements[id]={
        events:{},dataset:{},querySelector(){return this;},value:'2',textContent:'',setAttribute(){},addEventListener(n,f){this.events[n]=f;}};
      global.document={querySelector:id=>elements[id],addEventListener(){}};
      global.window={isSecureContext:true,addEventListener(){}};
      global.location={protocol:'https:',host:'l4u.duckdns.org'};
      global.WebSocket=class {constructor(){sockets.push(this);}close(){this.closed=true;}};
      global.AudioContext=class {
        constructor(){this.currentTime=0;this.destination={};contexts.push(this);}
        createGain(){return this.gain={gain:{value:0,setTargetAtTime(v){this.value=v;}},connect:n=>this.gainTarget=n};}
        createDynamicsCompressor(){return this.compressor={threshold:{},knee:{},ratio:{},attack:{},release:{},connect:n=>this.output=n};}
        resume(){return Promise.resolve();}close(){this.closed=true;return Promise.resolve();}
        createBuffer(c,n,r){this.samples=new Float32Array(n);return {duration:n/r,getChannelData:()=>this.samples};}
        createBufferSource(){return {connect:n=>this.sourceTarget=n,start(){},stop(){}};}
      };
      require(process.argv[1]);
      (async()=>{
        await elements['#listen'].events.click();
        const ctx=contexts[0];
        if(ctx.gain.gain.value!==2)throw Error('default amplification missing');
        sockets[0].onmessage({data:new Int16Array([16384,-16384]).buffer});
        if(ctx.samples[0]!==.5||ctx.samples[1]!==-.5)throw Error('source PCM changed');
        if(ctx.sourceTarget!==ctx.gain||ctx.gainTarget!==ctx.compressor||ctx.output!==ctx.destination)throw Error('playback bypasses gain/compression');
        for(const [input,expected] of [['6',6],['0',0],['99',8],['NaN',2]]){
          elements['#listen-volume'].value=input;elements['#listen-volume'].events.input();
          if(ctx.gain.gain.value!==expected)throw Error('volume adjustment failed');
        }
        if(sockets.length!==1)throw Error('volume reopened stream');
        await elements['#listen'].events.click();
        if(!ctx.closed||!sockets[0].closed)throw Error('playback was not cleaned up');
      })().catch(e=>{console.error(e.message);process.exitCode=1;});
    '''
    result=subprocess.run([node,'-e',program,str(script)],capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr.decode()


@pytest.mark.parametrize('mode',['release','hidden','cancel','deny','stop-listen'])
def test_talk_pauses_and_restores_listening_and_has_separate_gain(mode):
    node=shutil.which('node')
    if not node:pytest.skip('node unavailable')
    from pathlib import Path
    script=Path(__file__).resolve().parents[1]/'icsee_ptz/static/audio.js'
    program=r'''
      const elements={},sockets=[],contexts=[],docEvents={};let resolveMic,rejectMic,stops=0;
      const mode=process.argv[2],flush=()=>new Promise(resolve=>setImmediate(resolve));
      for(const id of ['#listen','#talk','#audio-status','#listen-volume','#listen-volume-value','#talk-volume','#talk-volume-value'])elements[id]={
        events:{},dataset:{},querySelector(){return this;},value:'2',textContent:'',classList:{add(){},remove(){}},setPointerCapture(){},setAttribute(){},addEventListener(n,f){this.events[n]=f;}};
      global.document={hidden:false,querySelector:id=>elements[id],addEventListener(n,f){docEvents[n]=f;}};
      global.window={isSecureContext:true,AudioWorkletNode:class{},addEventListener(){}};
      global.location={protocol:'https:',host:'l4u.duckdns.org'};
      global.WebSocket=class {static OPEN=1;constructor(url){this.url=url;this.readyState=1;this.bufferedAmount=0;sockets.push(this);}send(){}close(){this.closed=true;}};
      const audioNode=()=>({connect(n){this.target=n;},disconnect(){this.disconnected=true;}});
      global.AudioWorkletNode=class {constructor(){Object.assign(this,audioNode());this.port={};}};
      global.AudioContext=class {
        constructor(){this.currentTime=0;this.destination={};this.audioWorklet={addModule:async()=>{}};contexts.push(this);}
        createGain(){return Object.assign(audioNode(),{gain:{value:0,setTargetAtTime(v){this.value=v;}}});}
        createDynamicsCompressor(){return Object.assign(audioNode(),{threshold:{},knee:{},ratio:{},attack:{},release:{}});}
        createMediaStreamSource(){return this.source=audioNode();}
        createBuffer(c,n,r){this.buffers=(this.buffers||0)+1;return {duration:n/r,getChannelData:()=>new Float32Array(n)};}
        createBufferSource(){return Object.assign(audioNode(),{start(){},stop(){}});}
        resume(){return Promise.resolve();}close(){this.closed=true;return Promise.resolve();}
      };
      Object.defineProperty(globalThis,'navigator',{value:{mediaDevices:{getUserMedia:()=>new Promise((yes,no)=>{resolveMic=yes;rejectMic=no;})}}});
      require(process.argv[1]);
      (async()=>{
        await elements['#listen'].events.click();
        const listenContext=contexts[0],packet={data:new Int16Array([1,2]).buffer};
        sockets[0].onmessage(packet);const before=listenContext.buffers;
        elements['#talk'].events.click();await flush();
        if(sockets[0].closed||!elements['#listen'].textContent.includes('일시정지'))throw Error('listen not paused');
        sockets[0].onmessage(packet);
        if(listenContext.buffers!==before)throw Error('room audio queued during talk');
        elements['#listen-volume'].value='4';elements['#listen-volume'].events.input();
        if(mode==='cancel')elements['#talk'].events.click();
        if(mode==='deny')rejectMic(Object.assign(Error('denied'),{name:'NotAllowedError'}));
        else resolveMic({getTracks:()=>[{stop(){stops++;}}]});
        await flush();
        if(mode!=='cancel'&&mode!=='deny'){
          sockets[1].onmessage({data:JSON.stringify({state:'ready'})});
          const gain=contexts[1].source.target;
          if(gain.gain.value!==1)throw Error('talk default gain incorrect');
          elements['#talk-volume'].value='6';elements['#talk-volume'].events.input();
          if(gain.gain.value!==6)throw Error('talk gain not adjustable');
          if(mode==='stop-listen')await elements['#listen'].events.click();
          if(mode==='hidden'){document.hidden=true;docEvents.visibilitychange();}
          else elements['#talk'].events.click();
          if(!gain.disconnected||!contexts[1].closed||!sockets[1].closed||stops!==1)throw Error('microphone resources leaked');
        }
        if(mode==='hidden'||mode==='stop-listen'){
          if(!sockets[0].closed||!listenContext.closed)throw Error('listening resumed after cancellation');
        } else {
          sockets[0].onmessage(packet);
          if(listenContext.buffers!==before+1||elements['#listen'].textContent!=='듣는 중')throw Error('listening did not resume');
          await elements['#listen'].events.click();
        }
        if(mode==='cancel'&&stops!==1)throw Error('late mic stream not stopped');
      })().catch(e=>{console.error(e.message);process.exitCode=1;});
    '''
    result=subprocess.run([node,'-e',program,str(script),mode],capture_output=True,timeout=10)
    assert result.returncode==0,result.stderr.decode()
