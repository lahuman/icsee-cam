"""User-requested single 10-second quiet synthetic tone; no recording."""
import json,math,struct,time
from icsee_ptz.audio import DVRIPTalk
from icsee_ptz.config import Config,safe_error

def main():
    talk=DVRIPTalk(Config.from_env())
    try:
        talk.open()
        start=time.monotonic()
        for packet in range(250):
            pcm=struct.pack('<320h',*(int(32767*.05*math.sin(2*math.pi*440*(packet*320+i)/8000)) for i in range(320)))
            talk.send_pcm(pcm)
            time.sleep(max(0,start+(packet+1)*.04-time.monotonic()))
        time.sleep(.2)
        print(json.dumps({'synthetic_tone_seconds':10,'frequency_hz':440,'amplitude':.05,
            'packets_sent':talk.packets,'start_acknowledged':talk.start_acknowledged,
            'microphone_used':False,'saved':False}))
        return 0
    except Exception as exc:
        print(json.dumps({'error':safe_error(exc),'packets_sent':talk.packets},ensure_ascii=False))
        return 2
    finally:
        talk.close()
        print(json.dumps({'talk_connection_closed':True}))

if __name__=='__main__':
    raise SystemExit(main())
