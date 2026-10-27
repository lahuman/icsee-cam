import asyncio,json
from websockets.asyncio.client import connect
async def main():
    async with connect('ws://127.0.0.1:8000/audio/listen',origin='http://127.0.0.1:8000',max_size=16384) as ws:
        metadata=json.loads(await asyncio.wait_for(ws.recv(),5))
        block=await asyncio.wait_for(ws.recv(),10)
        print(json.dumps({'listen':metadata,'pcm_bytes':len(block),'stored':False}))
    async with connect('ws://127.0.0.1:8000/audio/talk',origin='http://127.0.0.1:8000',max_size=16384) as ws:
        metadata=json.loads(await asyncio.wait_for(ws.recv(),5))
        print(json.dumps({'talk':metadata,'microphone_used':False}))
        await ws.send('stop')
asyncio.run(main())
