"""Versioned account files; passwords are entered locally and never returned."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile

from .config import CameraError
from .security import ROLES

ITERATIONS = 600_000


def password_record(password):
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise CameraError('비밀번호는 8~128자여야 합니다.')
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), ITERATIONS).hex()
    return {'algorithm': 'pbkdf2_sha256', 'iterations': ITERATIONS, 'salt': salt, 'digest': digest}


def check_password(password, record):
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(record['salt']), record['iterations']).hex()
    return hmac.compare_digest(digest, record['digest'])


def validate(data):
    if data.get('version') != 2 or not isinstance(data.get('users'), list) or not 1 <= len(data['users']) <= 128:
        raise ValueError('invalid account schema')
    users = {}
    for user in data['users']:
        name = user['username']
        password = user['password_hash']
        if (not isinstance(name, str) or not 1 <= len(name) <= 64 or name in users
                or user['role'] not in ROLES or type(user['enabled']) is not bool
                or password['algorithm'] != 'pbkdf2_sha256'
                or type(password['iterations']) is not int or not 200000 <= password['iterations'] <= 2000000
                or len(bytes.fromhex(password['salt'])) != 16
                or len(bytes.fromhex(password['digest'])) != 32):
            raise ValueError('invalid account')
        users[name] = user
    if not any(u['enabled'] and u['role'] == 'admin' for u in users.values()):
        raise ValueError('at least one enabled admin is required')
    return users


def read_accounts(path):
    try:
        data = json.loads(Path(path).read_text())
        if 'version' not in data and 'username' in data and 'password' in data:
            data = {'version': 2, 'users': [{'username': data['username'], 'role': 'admin',
                    'enabled': True, 'password_hash': password_record(data['password'])}]}
        return validate(data)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise CameraError('웹 계정 설정을 읽을 수 없습니다. 계정 형식과 활성 관리자 계정을 확인하세요.') from None


def write_accounts(path, users):
    path = Path(path)
    data = {'version': 2, 'users': list(users.values())}
    try:
        validate(data)
    except (ValueError, KeyError, TypeError):
        raise CameraError('계정 설정이 올바르지 않거나 마지막 관리자를 비활성화하려고 했습니다.') from None
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Preserve container-readable permissions inside the private config directory.
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix='.accounts-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def manage_accounts(args):
    import fcntl
    import getpass
    path = Path(args.auth_config)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        users = read_accounts(path) if path.exists() else {}
        if args.account_action == 'list':
            return {'users': [{k: u[k] for k in ('username', 'role', 'enabled')} for u in users.values()]}
        if args.account_action == 'set':
            if not args.username or not 1 <= len(args.username) <= 64:
                raise CameraError('ID는 1~64자여야 합니다.')
            old = users.get(args.username)
            if old and not args.reset_password:
                password = old['password_hash']
            else:
                first = getpass.getpass('새 웹 비밀번호: ')
                if first != getpass.getpass('비밀번호 확인: '):
                    raise CameraError('비밀번호가 일치하지 않습니다.')
                password = password_record(first)
            users[args.username] = {'username': args.username, 'role': args.role,
                                   'enabled': True, 'password_hash': password}
        elif args.account_action == 'disable':
            if args.username not in users:
                raise CameraError('계정이 없습니다.')
            users[args.username] = {**users[args.username], 'enabled': False}
        elif args.account_action != 'migrate':
            raise CameraError('지원하지 않는 계정 작업입니다.')
        write_accounts(path, users)
        return {'state': 'saved', 'users': len(users)}
