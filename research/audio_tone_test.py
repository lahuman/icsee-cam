"""One quiet, one-second synthetic tone. No microphone and no audio files."""
import json,math,struct,time
from icsee_ptz.audio import DVRIPTalk
from icsee_ptz.config import Config,safe_error
c=Config.from_env();talk=DVRIPTalk(c)
try:
    talk.open();start=time.monotonic()
    for packet in range(25):
        samples=[int(32767*.05*math.sin(2*math.pi*440*(packet*320+i)/8000)) for i in range(320)]
        talk.send_pcm(struct.pack('<320h',*samples))
        time.sleep(max(0,start+(packet+1)*.04-time.monotonic()))
    time.sleep(.2)
    print(json.dumps({'synthetic_tone_seconds':1,'amplitude':.05,'packets_sent':talk.packets,
        'start_acknowledged':talk.start_acknowledged,'closed_before_cleanup':talk.closed.is_set(),
        'microphone_used':False,'saved':False}))
except Exception as e: print(json.dumps({'error':safe_error(e)},ensure_ascii=False))
finally:talk.close()
