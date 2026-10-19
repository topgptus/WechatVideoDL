"""Shared, admin-managed Yuanbao browser credentials.

Never log, echo, export or persist these values outside their configured
private files. File updates are atomic and never reveal old/new secret bytes.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path


MAX_COOKIES_BYTES = 512 * 1024
MAX_SESSION_BYTES = 1024 * 1024


class CredentialImportError(ValueError):
    """Public-safe validation identifier, never includes uploaded content."""


def parse_netscape_cookies(content: bytes) -> list[dict]:
    if not content or len(content) > MAX_COOKIES_BYTES:
        raise CredentialImportError("COOKIE_SIZE_INVALID")
    try:
        decoded = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise CredentialImportError("COOKIE_ENCODING_INVALID") from None
    out: list[dict] = []
    now = int(time.time())
    for row in decoded.splitlines():
        line = row.strip()
        http_only = False
        if line.startswith('#HttpOnly_'):
            line = line[len('#HttpOnly_'):]
            http_only = True
        elif not line or line.startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) < 7:
            continue
        domain, _, path, secure, expires, name = parts[:6]
        value = '\t'.join(parts[6:])
        domain_check = domain.lstrip('.').lower()
        if not (domain_check == 'yuanbao.tencent.com' or
                'yuanbao.tencent.com'.endswith('.' + domain_check)):
            continue
        if not name or not value or not path.startswith('/') or secure.upper() not in ('TRUE','FALSE'):
            continue
        try:
            exp = int(expires or '0')
        except ValueError:
            continue
        if exp and exp < now:
            continue
        if len(out) >= 300:
            raise CredentialImportError('COOKIE_COUNT_EXCEEDED')
        item = {'name': name, 'value': value, 'domain': domain, 'path': path,
                'secure': secure.upper() == 'TRUE', 'httpOnly': http_only}
        if exp:
            item['expires'] = exp
        out.append(item)
    if not out:
        raise CredentialImportError('COOKIE_NO_VALID_ENTRIES')
    return out


def parse_session(content: bytes) -> dict:
    if not content or len(content) > MAX_SESSION_BYTES:
        raise CredentialImportError('SESSION_SIZE_INVALID')
    try:
        value = json.loads(content.decode('utf-8-sig'))
    except (UnicodeDecodeError, ValueError):
        raise CredentialImportError('SESSION_JSON_INVALID') from None
    if not isinstance(value, dict) or not value:
        raise CredentialImportError('SESSION_FORMAT_INVALID')
    keys = ('ua','platform','language','cookie','localStorage','sessionStorage')
    if not any(k in value for k in keys):
        raise CredentialImportError('SESSION_FORMAT_INVALID')
    for key in ('ua','platform','language','cookie'):
        if key in value and (not isinstance(value[key], str) or len(value[key]) > 65536):
            raise CredentialImportError('SESSION_FORMAT_INVALID')
    for key in ('localStorage','sessionStorage'):
        if key in value:
            obj = value[key]
            if not isinstance(obj, dict) or len(obj) > 512:
                raise CredentialImportError('SESSION_FORMAT_INVALID')
            if any(not isinstance(k, str) or not isinstance(v, (str,int,float,bool,type(None)))
                   or len(k)>512 or len(str(v))>65536 for k,v in obj.items()):
                raise CredentialImportError('SESSION_FORMAT_INVALID')
    return value


def atomic_import(path: Path, content: bytes) -> None:
    """Replace only a private credential file; failed validation leaves old file intact."""
    path = Path(path)
    if path.parent.is_symlink() or not path.parent.is_dir():
        raise CredentialImportError('CREDENTIAL_STORAGE_UNAVAILABLE')
    target = None
    try:
        fd, filename = tempfile.mkstemp(prefix='.yuanbao-import-',suffix='.tmp',dir=path.parent)
        target = Path(filename)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(target, 0o600)
        os.replace(target, path)
    finally:
        if target is not None:
            target.unlink(missing_ok=True)


def credential_status(cookie_path: Path, session_path: Path) -> dict:
    """Safe counts/presence only; never return names, domains or tokens."""
    status = {'cookies': 'missing', 'session': 'missing', 'cookie_count': 0}
    try:
        if cookie_path.is_file():
            status['cookie_count'] = len(parse_netscape_cookies(cookie_path.read_bytes()))
            status['cookies'] = 'ready'
    except (CredentialImportError, OSError):
        status['cookies'] = 'invalid'
    try:
        if session_path.is_file():
            parse_session(session_path.read_bytes())
            status['session'] = 'ready'
    except (CredentialImportError, OSError):
        status['session'] = 'invalid'
    return status
