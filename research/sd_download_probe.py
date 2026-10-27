import json,struct,time
from datetime import datetime,timedelta
from pathlib import Path
from icsee_ptz.config import Config,safe_error
from icsee_ptz.dvrip import DVRIPClient,HEADER
from icsee_ptz.timelapse import KST

def main():
    client=None
    try:
        client=DVRIPClient(Config.from_env(timeout=5));client.__enter__()
        now=datetime.now(KST)
        q={'BeginTime':(now-timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S'),'EndTime':now.strftime('%Y-%m-%d %H:%M:%S'),'Channel':0,'DriverTypeMask':'0x0000FFFF','Event':'*','StreamType':'0x00000000','Type':'h264'}
        files=client._exchange(1440,{'Name':'OPFileQuery','SessionID':f'0x{client.session:08X}','OPFileQuery':q})['OPFileQuery']
        record=files[1];start=datetime.strptime(record['BeginTime'],'%Y-%m-%d %H:%M:%S')
        params={'Parameter':{'PlayMode':'ByTime','FileName':record['FileName'],'StreamType':0,'Value':0,'Channel':0,'TransMode':'TCP'},'StartTime':record['BeginTime'],'EndTime':(start+timedelta(seconds=10)).strftime('%Y-%m-%d %H:%M:%S')}
        def data(action):return {'Name':'OPPlayBack','SessionID':f'0x{client.session:08X}','OPPlayBack':{'Action':action,**params}}
        ack=client._exchange(1424,data('Claim'));print(json.dumps({'claim_ret':ack.get('Ret'),'requested_start':record['BeginTime'],'record_end':record['EndTime']}),flush=True)
        body=json.dumps(data('DownloadStart')).encode()+b'\n\0';client.sock.sendall(HEADER.pack(255,0,client.session,client.sequence,1420,len(body))+body);client.sequence+=1
        output=bytearray();packets=[];deadline=time.monotonic()+8
        while time.monotonic()<deadline and len(output)<2*1024**2:
            client._deadline=deadline
            head=HEADER.unpack(client._receive(20));length=head[-1]
            if length>1024**2:raise ValueError('large packet')
            payload=client._receive(length) if length else b''
            if len(packets)<8:packets.append({'message':head[-2],'length':length,'prefix':payload[:16].hex() if head[-2] in (1422,1426) else 'json'})
            if head[-2] in (1422,1426):output.extend(payload)
            if not length:break
        Path('/private/tmp/icsee-sd-probe.raw').write_bytes(output)
        print(json.dumps({'packets':packets,'bytes':len(output)}),flush=True)
    except Exception as exc:print(json.dumps(safe_error(exc)),flush=True)
    finally:
        if client:client.close()
if __name__=='__main__':main()
