"""Read-only SD searches and bounded, on-demand H.264 preview downloads."""
import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from .config import CameraError, AuthenticationError, ProtocolError
from .dvrip import DVRIPClient, HEADER, MAX_BODY
from .timelapse import KST

FMT='%Y-%m-%d %H:%M:%S'
MAX_TRANSFER=32*1024**2
CACHE_LIMIT=128*1024**2


def parse_time(value):
    try:
        parsed=datetime.strptime(value,FMT)
        if parsed.strftime(FMT)!=value: raise ValueError()
        return parsed
    except (ValueError,TypeError):
        raise CameraError('녹화 시각 형식이 올바르지 않습니다.') from None


def query(config, start, end):
    begin,finish=parse_time(start),parse_time(end)
    if not begin<finish or finish-begin>timedelta(days=1):
        raise CameraError('한 번에 최대 하루의 녹화를 조회할 수 있습니다.')
    with DVRIPClient(config) as client:
        reply=client._exchange(1440,{'Name':'OPFileQuery','SessionID':f'0x{client.session:08X}',
            'OPFileQuery':{'BeginTime':start,'EndTime':end,'Channel':0,'DriverTypeMask':'0x0000FFFF',
                           'Event':'*','StreamType':'0x00000000','Type':'h264'}})
    code=reply.get('Ret')
    if code in (103,105,106,107,203): raise AuthenticationError('SD 녹화 조회 인증/권한 오류입니다.')
    if code in (109,119): return [],False
    if code not in (100,110,111): raise ProtocolError('SD 녹화 목록 조회에 실패했습니다.')
    rows=reply.get('OPFileQuery') or []
    if not isinstance(rows,list) or len(rows)>512: raise ProtocolError('녹화 목록 크기가 올바르지 않습니다.')
    result=[]
    for row in rows:
        if not isinstance(row,dict): continue
        name=row.get('FileName','')
        if not isinstance(name,str) or len(name)>512 or not name.startswith('/idea') or not name.endswith('.h264') or '..' in name:
            continue
        a,b=parse_time(row.get('BeginTime')),parse_time(row.get('EndTime'))
        if a>=finish or b<=begin or b<=a: continue
        result.append({'name':name,'start':a.strftime(FMT),'end':b.strftime(FMT),
                       'event':'움직임' if '[M]' in name else '일반 녹화'})
    return result,len(rows)>=64 or code==111


class Demux:
    def __init__(self, seconds):
        self.buffer=bytearray();self.video=bytearray();self.audio=bytearray()
        self.fps=12;self.frames=0;self.seconds=seconds;self.started=False
        self.first_time=None
    @property
    def done(self): return self.frames>=self.fps*self.seconds
    def feed(self,data):
        self.buffer.extend(data)
        while len(self.buffer)>=8 and not self.done:
            if self.buffer[:3]!=b'\0\0\1' or self.buffer[3] not in (0xFC,0xFD,0xFE,0xFA,0xF9):
                del self.buffer[0];continue
            kind=self.buffer[3];header=16 if kind in (0xFC,0xFE) else 8
            if len(self.buffer)<header:return
            length=struct.unpack_from('<I',self.buffer,12 if header==16 else 4)[0] if kind not in (0xFA,0xF9) else struct.unpack_from('<H',self.buffer,6)[0]
            if length>8*1024**2:raise ProtocolError('SD 프레임 크기 제한을 초과했습니다.')
            if len(self.buffer)<header+length:return
            head=bytes(self.buffer[:header]);payload=bytes(self.buffer[header:header+length]);del self.buffer[:header+length]
            if kind in (0xFC,0xFE):
                if head[4]&15!=2:raise ProtocolError('현재 SD 미리보기는 H.264 녹화를 지원합니다.')
                if not self.started:
                    packed=struct.unpack_from('<I',head,8)[0]
                    try:self.first_time=datetime(2000+(packed>>26&63),packed>>22&15,packed>>17&31,packed>>12&31,packed>>6&63,packed&63)
                    except ValueError:pass
                self.fps=head[5]&31 or 12;self.started=True
            if not self.started:continue
            if kind in (0xFC,0xFD,0xFE):self.video.extend(payload);self.frames+=1
            elif kind==0xFA and head[4]&15==14 and head[5]==2:
                room=max(0,self.seconds*8000-len(self.audio));self.audio.extend(payload[:room])


def download(config,record,start,seconds,folder,cancel):
    from .media_runtime import media_slot
    with media_slot('batch'):
        return _download(config,record,start,seconds,folder,cancel)


def _download(config,record,start,seconds,folder,cancel):
    demux=Demux(seconds);transferred=0
    with DVRIPClient(config) as client:
        params={'Parameter':{'PlayMode':'ByTime','FileName':record['name'],'StreamType':0,'Value':0,'Channel':0,'TransMode':'TCP'},
                'StartTime':start,'EndTime':(parse_time(start)+timedelta(seconds=seconds)).strftime(FMT)}
        def payload(action):return {'Name':'OPPlayBack','SessionID':f'0x{client.session:08X}','OPPlayBack':{'Action':action,**params}}
        def send(action):
            body=json.dumps(payload(action)).encode()+b'\n\0'
            client.sock.sendall(HEADER.pack(255,0,client.session,client.sequence,1420,len(body))+body);client.sequence+=1
        claim=client._exchange(1424,payload('Claim'))
        if claim.get('Ret')!=100:raise ProtocolError('SD 영상 읽기 채널을 열지 못했습니다.')
        deadline=time.monotonic()+90
        keepalive=time.monotonic()+10
        try:
            send('DownloadStart')
            while not demux.done:
                if cancel.is_set():raise CameraError('영상 가져오기를 취소했습니다.')
                if time.monotonic()>=deadline:raise CameraError('영상 수신 제한 시간을 초과했습니다.')
                if time.monotonic()>=keepalive:
                    body=json.dumps({'Name':'KeepAlive','SessionID':f'0x{client.session:08X}'}).encode()+b'\n\0'
                    client.sock.sendall(HEADER.pack(255,0,client.session,client.sequence,1006,len(body))+body)
                    client.sequence+=1;keepalive=time.monotonic()+10
                client._deadline=min(deadline,time.monotonic()+config.timeout)
                magic,version,session,seq,message,length=HEADER.unpack(client._receive(20))
                if magic!=255 or version not in (0,1) or session!=client.session or length>MAX_BODY:
                    raise ProtocolError('SD 영상 패킷 헤더가 올바르지 않습니다.')
                data=client._receive(length) if length else b''
                if message in (1422,1426):
                    if not length:break
                    transferred+=length
                    if transferred>MAX_TRANSFER:raise CameraError('한 구간의 전송량 32MiB 제한을 초과했습니다.')
                    demux.feed(data)
                elif message in (1421,1007):
                    ack=json.loads(data.rstrip(b'\0\r\n '))
                    if ack.get('Ret')!=100:raise ProtocolError('SD 영상 읽기를 거부했습니다.')
                else:raise ProtocolError('예상하지 않은 SD 영상 응답입니다.')
        finally:
            try:send('DownloadStop')
            except OSError:pass
    if not demux.frames:raise CameraError('재생 가능한 SD 영상 프레임이 없습니다.')
    requested=parse_time(start)
    if not demux.first_time or not requested-timedelta(seconds=2)<=demux.first_time<requested+timedelta(seconds=seconds+1):
        raise CameraError('카메라가 다른 시각의 영상을 반환했습니다. 시작 시각을 바꾸어 다시 조회해 주세요.')
    if cancel.is_set():raise CameraError('영상 가져오기를 취소했습니다.')
    ffmpeg,ffprobe=shutil.which('ffmpeg'),shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:raise CameraError('SD 재생에는 ffmpeg와 ffprobe가 필요합니다.')
    folder=Path(folder)
    video=folder/'video.h264';video.write_bytes(demux.video)
    audio=folder/'audio.alaw';audio.write_bytes(demux.audio)
    output=folder/'preview.mp4'
    command=[ffmpeg,'-hide_banner','-loglevel','error','-nostdin','-y','-r',str(demux.fps),'-f','h264','-i',str(video)]
    if demux.audio:command+=['-f','alaw','-ar','8000','-ac','1','-i',str(audio)]
    command+=['-map','0:v:0','-c:v','copy']
    if demux.audio:command+=['-map','1:a:0','-c:a','aac','-shortest']
    command+=['-t',str(seconds),'-movflags','+faststart',str(output)]
    from .processes import run_media
    result=run_media(command,timeout=30,cancel=cancel,reserve_path=folder)
    if result.returncode:raise CameraError('SD 영상 변환에 실패했습니다.')
    probe=run_media([ffprobe,'-v','quiet','-show_streams','-show_format','-of','json',str(output)],capture=True,timeout=10,cancel=cancel)
    try:
        info=json.loads(probe.stdout);duration=float(info['format']['duration'])
        if probe.returncode or duration<=0 or not any(s.get('codec_name')=='h264' for s in info['streams']):raise ValueError()
    except (ValueError,KeyError):raise CameraError('SD 영상 검증에 실패했습니다.') from None
    return {'duration':duration,'actual_start':demux.first_time.strftime(FMT),
            'received_bytes':transferred,'audio':any(s.get('codec_type')=='audio' for s in info['streams'])}


class SDService:
    def __init__(self,config,root):
        self.config=config;self.root=Path(root);self.records={};self.lock=threading.Lock();self.guard=threading.Lock()
    def search(self,start,end):
        rows,more=query(self.config,start,end);items=[]
        with self.guard:
            now=time.monotonic()
            self.records={key:value for key,value in self.records.items() if now-value[0]<900}
            for record in rows:
                key=hashlib.sha256(record['name'].encode()).hexdigest()[:24]
                self.records[key]=(now,record)
                items.append({'id':key,'start':record['start'],'end':record['end'],'event':record['event']})
        cursor=max((r['start'] for r in rows),default=start)
        return {'recordings':items,'more':more,'next_start':cursor,'timezone':'Asia/Seoul'}
    def prepare(self,key,start,seconds,cancel):
        import fcntl
        from .media_runtime import MediaBusy
        if cancel.is_set():
            raise CameraError('영상 가져오기를 취소했습니다.')
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / '.download.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise MediaBusy('다른 SD 구간을 가져오는 중입니다.') from None
            return self._prepare(key,start,seconds,cancel)

    def _prepare(self,key,start,seconds,cancel):
        if cancel.is_set():raise CameraError('영상 가져오기를 취소했습니다.')
        if not isinstance(seconds,int) or not 1<=seconds<=60:raise CameraError('구간 길이는 1~60초여야 합니다.')
        if not self.lock.acquire(blocking=False):raise CameraError('다른 SD 구간을 가져오는 중입니다.')
        try:
            with self.guard:item=self.records.get(key)
            if not item or time.monotonic()-item[0]>900:raise CameraError('녹화 목록을 다시 조회해 주세요.')
            record=item[1];begin=parse_time(start)
            if not parse_time(record['start'])<=begin<parse_time(record['end']):raise CameraError('선택 시각이 녹화 구간 밖입니다.')
            seconds=min(seconds,max(1,int((parse_time(record['end'])-begin).total_seconds())))
            token=hashlib.sha256(f'bytime-v1:{key}:{start}:{seconds}'.encode()).hexdigest()
            self.root.mkdir(parents=True,exist_ok=True)
            target=self.root/(token+'.mp4');meta=self.root/(token+'.json')
            if target.is_file() and meta.is_file() and time.time()-target.stat().st_mtime<86400:
                return {'id':token,**json.loads(meta.read_text()),'cached':True}
            for path in self.root.glob('*.mp4'):
                if time.time()-path.stat().st_mtime>86400:
                    path.unlink();path.with_suffix('.json').unlink(missing_ok=True)
            used=sum(p.stat().st_size for p in self.root.glob('*.mp4'))
            if used+MAX_TRANSFER>CACHE_LIMIT or shutil.disk_usage(self.root).free<512*1024**2:
                raise CameraError('SD 미리보기 저장 공간이 부족합니다. 임시 파일을 정리해 주세요.')
            with tempfile.TemporaryDirectory(dir=self.root,prefix='.fetch-') as folder:
                result=download(self.config,record,start,seconds,folder,cancel)
                if cancel.is_set():raise CameraError('영상 가져오기를 취소했습니다.')
                Path(folder,'preview.mp4').replace(target)
            result.update(start=start,requested_seconds=seconds)
            meta.write_text(json.dumps(result))
            return {'id':token,**result,'cached':False}
        finally:self.lock.release()
