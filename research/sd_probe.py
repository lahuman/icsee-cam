import json
from datetime import datetime, timedelta
from icsee_ptz.config import Config, safe_error
from icsee_ptz.dvrip import DVRIPClient
from icsee_ptz.timelapse import KST

def main():
    now=datetime.now(KST)
    try:
        with DVRIPClient(Config.from_env(timeout=5)) as client:
            response=client._exchange(1440, {'Name':'OPFileQuery','SessionID':f'0x{client.session:08X}',
                'OPFileQuery':{'BeginTime':(now-timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S'),
                'EndTime':now.strftime('%Y-%m-%d %H:%M:%S'),'Channel':0,'DriverTypeMask':'0x0000FFFF',
                'Event':'*','StreamType':'0x00000000','Type':'h264'}})
            records=response.get('OPFileQuery',[])
            print(json.dumps({'ret':response.get('Ret'),'count':len(records),'first':records[:2],
                              'last':records[-1:]},ensure_ascii=False,indent=2))
    except Exception as exc:
        print(json.dumps(safe_error(exc),ensure_ascii=False))

if __name__=='__main__':main()
