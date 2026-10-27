"""Bounded live receive/reconnect test. No media files, motor or speaker output."""
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from websockets.asyncio.client import connect

from icsee_ptz.config import Config, safe_error, redact
from icsee_ptz.dvrip import DVRIPClient


def video_check():
    start = time.monotonic()
    size = frames = 0
    tail = b""
    with requests.get("http://127.0.0.1:8000/video", stream=True, timeout=(3, 8)) as response:
        response.raise_for_status()
        for block in response.iter_content(8192):
            size += len(block)
            data = tail + block
            frames += data.count(b"\xff\xd9")
            tail = data[-1:]
            if time.monotonic() - start >= 8:
                break
    return {"seconds": round(time.monotonic() - start, 2), "bytes_received": size,
            "jpeg_end_markers": frames, "stored": False}


async def audio_check():
    size = 0
    async with connect("ws://127.0.0.1:8000/audio/listen",
                       origin="http://127.0.0.1:8000", max_size=16384) as ws:
        ready = json.loads(await asyncio.wait_for(ws.recv(), 5))
        if ready.get("state") != "ready":
            raise RuntimeError("listen not ready")
        for _ in range(20):
            block = await asyncio.wait_for(ws.recv(), 8)
            if not isinstance(block, bytes):
                raise RuntimeError("audio data missing")
            size += len(block)
    return {"pcm_bytes_received": size, "stored": False, "played_on_mac": False}


def storage_check(config):
    with DVRIPClient(config) as client:
        return client.read("StorageInfo")


async def main():
    config = Config.from_env()
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "rounds": [],
              "motor_used": False, "speaker_used": False, "microphone_used": False,
              "media_saved": False}
    for _ in range(2):
        result = await asyncio.gather(asyncio.to_thread(video_check), audio_check(), return_exceptions=True)
        report["rounds"].append({name: safe_error(value) if isinstance(value, Exception) else value
                                 for name, value in zip(("video", "audio"), result)})
        # Give the server time to notice the closed audio receiver and free its slot.
        await asyncio.sleep(2)
    try:
        report["storage_after"] = await asyncio.to_thread(storage_check, config)
    except Exception as exc:
        report["storage_error"] = safe_error(exc)
    cleaned = redact(report, config.password)
    Path("research/live-stability-check.json").write_text(json.dumps(cleaned, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(cleaned, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
