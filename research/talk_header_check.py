import json
from icsee_ptz.config import Config,safe_error
from icsee_ptz.audio import DVRIPTalk
from icsee_ptz.dvrip import HEADER
c=Config.from_env();t=DVRIPTalk(c)
original=t.client._receive
def receive(length):
    data=original(length)
    if length==20:
        magic,version,session,seq,message,size=HEADER.unpack(data)
        print(json.dumps({'header':{'magic':magic,'version':version,'sequence':seq,'message':message,'body_bytes':size}}))
    return data
t.client._receive=receive
try: print(t.open())
except Exception as e: print(safe_error(e))
finally:t.close()
