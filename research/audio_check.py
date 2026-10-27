"""No microphone, no audio files: receive a short PCM block and claim/close talk."""
import asyncio,json,logging
from icsee_ptz.audio import listen_pcm,DVRIPTalk
from icsee_ptz.config import Config,safe_error
logging.disable(logging.CRITICAL)
c=Config.from_env()
async def check():
    stream=listen_pcm(c)
    try:
        block=await anext(stream)
        print(json.dumps({'listen_pcm_bytes':len(block),'sample_rate':16000,'saved':False}))
    except Exception as e:print(json.dumps({'listen_error':safe_error(e)}))
    finally:await stream.aclose()
asyncio.run(check())
talk=DVRIPTalk(c)
try:print(json.dumps({'talk_handshake':talk.open(),'audio_sent':False}))
except Exception as e:print(json.dumps({'talk_error':safe_error(e)},ensure_ascii=False))
finally:talk.close()
