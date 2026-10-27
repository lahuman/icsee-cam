import asyncio
from pathlib import Path
import threading
import anyio

from fastapi import WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from .audio import DVRIPTalk, listen_pcm, MAX_TALK_SECONDS, PCM_PACKET_BYTES
from .config import safe_error
from .reconnect import Backoff, retryable


class AudioService:
    def __init__(self, talk_factory, listen_factory):
        self.talk_factory = talk_factory
        self.listen_factory = listen_factory
        self.talk_slot = threading.Lock()
        from .media_runtime import SharedStream
        self.listen_stream = SharedStream(self.listen_factory)
        self.tasks = set()

    async def shutdown(self):
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self.listen_stream.close()


def attach_audio(app, config, port, *, talk_factory=None, listen_factory=None, container_mode=False, access=None):
    from .web_access import WebAccess
    access=access or WebAccess(port,container_mode)
    service = AudioService(talk_factory or (lambda: DVRIPTalk(config)),
                           listen_factory or (lambda: listen_pcm(config)))

    async def allowed(ws):
        host = ws.headers.get("host", "")
        valid = (access.origin_allowed(host,ws.headers.get('origin'))
            and ws.client and access.client_allowed(ws.client.host))
        if not valid:
            await ws.close(code=1008)
        return valid

    async def close(ws):
        try:
            await ws.close()
        except (RuntimeError, WebSocketDisconnect):
            pass

    async def report_error(ws, exc):
        try:
            await ws.send_json({"state": "error", "error": safe_error(exc)})
        except (RuntimeError, WebSocketDisconnect):
            pass

    @app.get("/audio.js")
    def audio_js():
        return FileResponse(Path(__file__).with_name("static") / "audio.js", media_type="text/javascript")

    @app.get("/audio-worklet.js")
    def worklet_js():
        return FileResponse(Path(__file__).with_name("static") / "audio-worklet.js", media_type="text/javascript")

    @app.websocket("/audio/listen")
    async def listen(ws: WebSocket):
        if not await allowed(ws):
            return
        stream = None
        workers = []
        task = asyncio.current_task()
        service.tasks.add(task)
        try:
            await ws.accept()
            stream = service.listen_stream.subscribe()
            await ws.send_json({"state": "ready", "sample_rate": 16000, "channels": 1,
                                "format": "s16le", "recording": False})
            async def send_audio():
                nonlocal stream
                backoff = Backoff()
                while True:
                    try:
                        async for block in stream:
                            await ws.send_bytes(block)
                            backoff.reset()
                        return
                    except Exception as exc:
                        if not retryable(exc):
                            raise
                        await stream.aclose()
                        delay = backoff.next()
                        await ws.send_json({"state": "waiting", "retry_in_seconds": delay})
                        await asyncio.sleep(delay)
                        stream = service.listen_stream.subscribe()

            async def wait_disconnect():
                # Receive independently: a stalled RTSP read must not delay
                # releasing the listener slot after the browser closes.
                while True:
                    message = await ws.receive()
                    if message["type"] == "websocket.disconnect":
                        return

            workers = [asyncio.create_task(send_audio()), asyncio.create_task(wait_disconnect())]
            done, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
            for worker in done:
                worker.result()
        except WebSocketDisconnect:
            pass
        except Exception as exc:
            await report_error(ws, exc)
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    for worker in workers:
                        worker.cancel()
                    if workers:
                        await asyncio.gather(*workers, return_exceptions=True)
                    if stream:
                        await stream.aclose()
                finally:
                    service.tasks.discard(task)
                    await close(ws)

    @app.websocket("/audio/talk")
    async def talk(ws: WebSocket):
        if not await allowed(ws):
            return
        if not service.talk_slot.acquire(blocking=False):
            await ws.close(code=1013)
            return
        client = None
        task = asyncio.current_task()
        service.tasks.add(task)
        try:
            await ws.accept()
            client = service.talk_factory()
            metadata = await asyncio.to_thread(client.open)
            await ws.send_json(metadata)
            started = asyncio.get_running_loop().time()
            while True:
                remaining = MAX_TALK_SECONDS - (asyncio.get_running_loop().time() - started)
                if remaining <= 0:
                    break
                # Missing browser samples/heartbeats end the dedicated talk connection.
                message = await asyncio.wait_for(ws.receive(), timeout=min(1.0, remaining))
                if message["type"] == "websocket.disconnect" or message.get("text") == "stop":
                    break
                pcm = message.get("bytes")
                if pcm is None or len(pcm) != PCM_PACKET_BYTES:
                    await ws.close(code=1009)
                    break
                await asyncio.to_thread(client.send_pcm, pcm)
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
        except Exception as exc:
            await report_error(ws, exc)
        finally:
            with anyio.CancelScope(shield=True):
                try:
                    if client:
                        await asyncio.to_thread(client.close)
                finally:
                    service.tasks.discard(task)
                    service.talk_slot.release()
                    await close(ws)

    return service
