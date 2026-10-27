"""Authenticated sessions with revocable active ASGI connections."""
import asyncio
import anyio
import json
from pathlib import Path
import secrets
import threading
import time

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.requests import HTTPConnection

from .audit import event
from .accounts import password_record, check_password, read_accounts
from .config import CameraError
from .security import ClientIdentity, RequestLimits, ROLES, required_role, request_category

COOKIE = 'icsee_session'
SESSION_SECONDS = 8 * 3600


class WebAuth:
    def __init__(self, username, password):
        if not isinstance(username, str) or not 1 <= len(username) <= 64:
            raise CameraError('웹 ID는 1~64자여야 합니다.')
        self.users = {username: {'username': username, 'role': 'admin', 'enabled': True,
                                'password_hash': password_record(password)}}
        self.sessions = {}
        self.session_users = {}
        self.connections = {}
        self.lock = threading.RLock()
        self.identity = ClientIdentity()
        self.limits = RequestLimits()
        self.path = None
        self.signature = None
        self.next_reload = 0
        self.login_slots = asyncio.Semaphore(4)
        self.dummy = password_record(secrets.token_urlsafe(24))

    @classmethod
    def from_file(cls, path):
        auth = cls('bootstrap', secrets.token_urlsafe(24))
        auth.users = read_accounts(path)
        auth.path = Path(path)
        auth.signature = auth._signature()
        return auth

    def _signature(self):
        stat = self.path.stat()
        return stat.st_ino, stat.st_mtime_ns, stat.st_size

    def refresh(self):
        if self.path is None:
            return
        with self.lock:
            now = time.monotonic()
            if now < self.next_reload:
                return
            self.next_reload = now + 1
            try:
                signature = self._signature()
                if signature == self.signature:
                    return
                users = read_accounts(self.path)
            except CameraError:
                users, signature = {}, None
            except OSError:
                users, signature = {}, None
            changed = {name for name in self.users if self.users[name] != users.get(name)}
            self.users, self.signature = users, signature
            for token, name in list(self.session_users.items()):
                if name in changed:
                    self.revoke(token)

    def credentials_valid(self, username, password):
        self.refresh()
        with self.lock:
            user = self.users.get(username)
            record = user['password_hash'] if user else self.dummy
        valid = check_password(password, record)
        return bool(valid and user and user['enabled'])

    def valid_token(self, token):
        with self.lock:
            user = self.users.get(self.session_users.get(token))
            return bool(user and user['enabled'] and self.sessions.get(token, 0) > time.monotonic())

    def valid(self, connection):
        return self.valid_token(connection.cookies.get(COOKIE, ''))

    def principal(self, token):
        with self.lock:
            user = self.users[self.session_users[token]]
            return {key: user[key] for key in ('username', 'role')}

    def issue(self, username=None):
        with self.lock:
            for token in list(self.sessions):
                if not self.valid_token(token):
                    self.revoke(token)
            if len(self.sessions) >= 128:
                self.revoke(next(iter(self.sessions)))
            username = username or next(iter(self.users))
            if username not in self.users or not self.users[username]['enabled']:
                raise CameraError('계정이 비활성화되었습니다.')
            token = secrets.token_urlsafe(32)
            self.sessions[token] = time.monotonic() + SESSION_SECONDS
            self.session_users[token] = username
            return token

    def revoke(self, token):
        with self.lock:
            self.sessions.pop(token, None)
            self.session_users.pop(token, None)
            for loop, event in list(self.connections.get(token, {}).values()):
                if not loop.is_closed():
                    loop.call_soon_threadsafe(event.set)

    def register(self, token, event):
        key = secrets.token_hex(8)
        with self.lock:
            self.connections.setdefault(token, {})[key] = (asyncio.get_running_loop(), event)
        return key

    def unregister(self, token, key):
        with self.lock:
            entries = self.connections.get(token, {})
            entries.pop(key, None)
            if not entries:
                self.connections.pop(token, None)


class WebAuthMiddleware:
    def __init__(self, app, auth):
        self.app, self.auth = app, auth

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        connection = HTTPConnection(scope)
        path, method = scope['path'], scope.get('method', 'GET')
        public = scope['type'] == 'http' and (path, method) in {
            ('/login', 'GET'), ('/login.js', 'GET'), ('/api/login', 'POST'), ('/healthz', 'GET')}
        if public:
            return await self.app(scope, receive, send)
        await asyncio.to_thread(self.auth.refresh)
        token = connection.cookies.get(COOKIE, '')
        if not self.auth.valid_token(token):
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            response = RedirectResponse('/login', status_code=303) if path == '/' else JSONResponse({'error': 'login required'}, status_code=401)
            return await response(scope, receive, send)
        principal = self.auth.principal(token)
        scope.setdefault('state', {})['principal'] = principal
        scope['state']['session_id'] = token
        if ROLES[principal['role']] < ROLES[required_role(path, method)]:
            if scope['type'] == 'websocket':
                return await send({'type': 'websocket.close', 'code': 1008})
            return await JSONResponse({'error': '이 작업을 수행할 권한이 없습니다.'}, status_code=403)(scope, receive, send)
        category = request_category(path, method, scope['type'] == 'websocket')
        if category and path.startswith(('/api/', '/audio/', '/video', '/snapshot')):
            if (not self.auth.limits.allow(category, principal['username'])
                    or not self.auth.limits.allow(category + '_session', token)):
                if scope['type'] == 'websocket':
                    return await send({'type': 'websocket.close', 'code': 1013})
                return await JSONResponse({'error': '요청이 너무 많습니다.'}, status_code=429,
                                          headers={'Retry-After': '60'})(scope, receive, send)
        if path == '/api/logout':
            return await self.app(scope, receive, send)
        revoked = asyncio.Event()
        key = self.auth.register(token, revoked)
        closed = False
        response_started = False

        async def guarded_send(message):
            nonlocal closed, response_started
            if message['type'] == 'websocket.close':
                closed = True
            elif not self.auth.valid_token(token):
                raise asyncio.CancelledError
            if message['type'] == 'http.response.start':
                response_started = True
            await send(message)

        async def expiry():
            while self.auth.valid_token(token) and not revoked.is_set():
                remaining = self.auth.sessions.get(token, 0) - time.monotonic()
                try:
                    await asyncio.wait_for(revoked.wait(), timeout=max(.001, min(1, remaining)))
                except asyncio.TimeoutError:
                    await asyncio.to_thread(self.auth.refresh)

        work = asyncio.create_task(self.app(scope, receive, guarded_send))
        watch = asyncio.create_task(expiry())
        try:
            done, _ = await asyncio.wait((work, watch), return_when=asyncio.FIRST_COMPLETED)
            if watch in done:
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
                if scope['type'] == 'websocket' and not closed:
                    await send({'type': 'websocket.close', 'code': 1008})
                elif scope['type'] == 'http' and not response_started:
                    await JSONResponse({'error': 'login required'}, status_code=401)(scope, receive, send)
            else:
                await work
        except asyncio.CancelledError:
            if scope["type"] != "websocket":
                raise
        finally:
            self.auth.unregister(token, key)
            watch.cancel()
            work.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(watch, work, return_exceptions=True)


def attach_auth(app, auth, *, secure_hosts=()):
    app.add_middleware(WebAuthMiddleware, auth=auth)
    app.state.auth = auth
    static = Path(__file__).with_name('static')

    @app.get('/healthz')
    def health():
        return {'status': 'ok'}

    @app.get('/login', response_class=HTMLResponse)
    def login_page():
        return (static / 'login.html').read_text()

    @app.get('/login.js')
    def login_script():
        return Response((static / 'login.js').read_text(), media_type='text/javascript')

    @app.get('/api/me')
    def me(request: Request):
        return request.state.principal

    @app.post('/api/login')
    async def login(request: Request):
        try:
            identity = auth.identity.identify(request)
        except CameraError:
            return JSONResponse({'error': '클라이언트 주소를 확인할 수 없습니다.'}, status_code=400)
        if not auth.limits.allow('login_ip', identity):
            event('rate_limited', category='login_ip', status=429, client=identity)
            return JSONResponse({'error': '잠시 후 다시 시도해 주세요.'}, status_code=429, headers={'Retry-After': '60'})
        body = bytearray()
        async for block in request.stream():
            if len(body) + len(block) > 4096:
                return JSONResponse({'error': '요청이 너무 큽니다.'}, status_code=413)
            body.extend(block)
        try:
            data = json.loads(body)
            username, password = data['username'], data['password']
            if not isinstance(username, str) or not isinstance(password, str) or len(username) > 64 or len(password) > 128:
                raise ValueError
        except (ValueError, KeyError, TypeError, RecursionError):
            return JSONResponse({'error': 'ID와 비밀번호를 입력해 주세요.'}, status_code=400)
        if not auth.limits.allow('login_account_ip', (identity, username)):
            return JSONResponse({'error': '잠시 후 다시 시도해 주세요.'}, status_code=429, headers={'Retry-After': '60'})
        try:
            await asyncio.wait_for(auth.login_slots.acquire(), timeout=.1)
        except asyncio.TimeoutError:
            return JSONResponse({'error': '인증 처리 중입니다. 잠시 후 다시 시도해 주세요.'}, status_code=503, headers={'Retry-After': '1'})
        try:
            valid = await asyncio.to_thread(auth.credentials_valid, username, password)
        finally:
            auth.login_slots.release()
        if not valid:
            event('login_failed', status=401, client=identity)
            return JSONResponse({'error': 'ID 또는 비밀번호가 올바르지 않습니다.'}, status_code=401)
        event('login_succeeded', status=200, client=identity)
        auth.revoke(request.cookies.get(COOKIE, ''))
        response = JSONResponse({'ok': True})
        response.set_cookie(COOKIE, auth.issue(username), max_age=SESSION_SECONDS, httponly=True,
                            samesite='strict', secure=request.url.scheme == 'https' or request.headers.get('host') in secure_hosts)
        return response

    @app.post('/api/logout')
    async def logout(request: Request):
        auth.revoke(request.cookies.get(COOKIE, ''))
        event('session_logout')
        response = JSONResponse({'ok': True})
        response.delete_cookie(COOKIE, httponly=True, samesite='strict')
        return response
