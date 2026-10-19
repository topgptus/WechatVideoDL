"""Extract safe WeChat Channels links from pasted share text."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

URL_PATTERN = re.compile(r'https?://[^\s<>"\'“”‘’]+', re.I)
HOSTS = {'weixin.qq.com','www.weixin.qq.com','channels.weixin.qq.com'}
TRAILING = '。；;，,、！!？?）)】]>'


def collect_video_urls(source: object, *, limit: int = 30) -> tuple[list[str], int]:
    if isinstance(source, str):
        if len(source) > 30_000:
            raise ValueError('BATCH_INPUT_TOO_LONG')
        candidates = [match.group(0).rstrip(TRAILING) for match in URL_PATTERN.finditer(source)]
    elif isinstance(source, list) and 1 <= len(source) <= 50 and all(isinstance(x, str) for x in source):
        candidates = [item.strip() for item in source]
    else:
        raise ValueError('INVALID_BATCH_URLS')
    if not candidates or len(candidates) > 50:
        raise ValueError('INVALID_BATCH_SIZE')
    unique = []
    seen = set()
    for address in candidates:
        try:
            parsed = urlsplit(address)
            if (parsed.scheme not in {'http','https'} or parsed.hostname not in HOSTS
                    or parsed.username or parsed.password or parsed.port not in (None,80,443)
                    or len(address)>2000 or not parsed.path or parsed.path == '/'):
                raise ValueError('INVALID_BATCH_URL')
        except (TypeError, ValueError):
            raise ValueError('INVALID_BATCH_URL') from None
        if address not in seen:
            unique.append(address)
            seen.add(address)
    if len(unique)>limit:
        raise ValueError('BATCH_TOO_LARGE')
    return unique, len(candidates)-len(unique)
