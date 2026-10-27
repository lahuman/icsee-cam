"""Cross-process media budgets and a bounded shared stream per camera."""
import asyncio
from contextlib import contextmanager
import fcntl
import threading
import anyio

from .config import CameraError
from .paths import runtime_root


@contextmanager
def media_slot(kind, root=None):
    root = root or runtime_root()
    root.mkdir(parents=True, exist_ok=True)
    # Reserve the live encoder independently: batch jobs cannot starve viewing.
    names = ['live'] if kind == 'live' else ['batch-0', 'batch-1']
    acquired = None
    for name in names:
        stream = (root / (name + '.lock')).open('a')
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            stream.close()
            continue
        acquired = stream
        break
    if acquired is None:
        raise MediaBusy('영상 변환 작업이 가득 찼습니다. 잠시 후 다시 시도해 주세요.')
    try:
        yield
    finally:
        acquired.close()


class MediaBusy(CameraError):
    pass


async def sync_stream(factory):
    """Never close a generator while its blocking next() is still running."""
    iterator = iter(factory())
    lock = threading.Lock()
    def read():
        with lock:
            return next(iterator, None)
    def close():
        with lock:
            method = getattr(iterator, 'close', None)
            if method:
                method()
    try:
        while True:
            block = await anyio.to_thread.run_sync(read)
            if block is None:
                return
            yield block
    finally:
        with anyio.CancelScope(shield=True):
            await anyio.to_thread.run_sync(close)


class SharedStream:
    def __init__(self, factory):
        self.factory = factory
        self.subscribers = set()
        self.producer = None
        self.lock = asyncio.Lock()

    def publish(self, value):
        for queue in tuple(self.subscribers):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(value)

    async def produce(self):
        stream = None
        try:
            stream = self.factory()
            async for frame in stream:
                self.publish(frame)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.publish(exc)
        finally:
            if stream:
                await stream.aclose()
            self.publish(None)

    async def subscribe(self):
        queue = asyncio.Queue(maxsize=2)
        async with self.lock:
            self.subscribers.add(queue)
            if self.producer is None or self.producer.done():
                self.producer = asyncio.create_task(self.produce())
        try:
            while True:
                item = await queue.get()
                if item is None:
                    return
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            with anyio.CancelScope(shield=True):
                async with self.lock:
                    self.subscribers.discard(queue)
                    if not self.subscribers and self.producer:
                        self.producer.cancel()
                        await asyncio.gather(self.producer, return_exceptions=True)
                        self.producer = None

    async def close(self):
        async with self.lock:
            if self.producer:
                self.producer.cancel()
                await asyncio.gather(self.producer, return_exceptions=True)
                self.producer = None


class ViewerRegistry:
    def __init__(self):
        self.active = {}
        self.lock = threading.Lock()

    def acquire(self, token, channel):
        with self.lock:
            if token not in self.active and len(self.active) >= 3:
                return False
            channels = self.active.setdefault(token, set())
            if channel in channels:
                return False
            channels.add(channel)
            return True

    def release(self, token, channel):
        with self.lock:
            channels = self.active.get(token, set())
            channels.discard(channel)
            if not channels:
                self.active.pop(token, None)

    def count(self):
        with self.lock:
            return len(self.active)


class ViewerMiddleware:
    def __init__(self, app, registry):
        self.app, self.registry = app, registry

    async def __call__(self, scope, receive, send):
        from fastapi.responses import JSONResponse
        path = scope.get('path', '')
        channel = None
        if path == '/snapshot':
            channel = 'snapshot'
        elif path == '/video' or (path.startswith('/api/') and (path.endswith('/video') or path.endswith('.mp4'))):
            channel = 'video'
        elif path == '/audio/listen':
            channel = 'listen'
        if channel is None or scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        token = scope.get('state', {}).get('session_id') or str(scope.get('client'))
        if not self.registry.acquire(token, channel):
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1013})
            return await JSONResponse({'error': '동시 시청은 최대 3명이며 세션당 영상·듣기는 각각 하나만 열 수 있습니다.'},
                                      status_code=429, headers={'Retry-After': '5'})(scope, receive, send)
        try:
            await self.app(scope, receive, send)
        finally:
            self.registry.release(token, channel)


from starlette.responses import StreamingResponse


class ManagedStreamingResponse(StreamingResponse):
    """Close the iterator even if cancellation occurs while ASGI send is blocked."""
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                close = getattr(self.body_iterator, 'aclose', None)
                if close:
                    await close()
