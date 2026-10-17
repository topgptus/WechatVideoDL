from __future__ import annotations

import re

# These are Yuanbao's explicit refusals; a video card can still provide a
# valid MP4 URL, so "nonempty response" is not sufficient for success.
_REFUSAL_PATTERNS = (
    re.compile(r"^(?:抱歉[，,。\s]*)?未找到相关视频[。！!\s]*$"),
    re.compile(r"^(?:抱歉[，,。\s]*)?这个视频(?:我)?(?:暂时|目前)?(?:还)?(?:没办法|无法)(?:理解|分析|读取)[^\\n]{0,70}$"),
    re.compile(r"^(?:抱歉[，,。\s]*)?(?:暂时|目前)?(?:无法|不能)(?:解析|理解|分析|读取)(?:该|这个|相关)?视频[^\\n]{0,70}$"),
)

def refusal_reason(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return "YUANBAO_EMPTY_RESPONSE"
    # Never classify a long, valid explanation that quotes a refusal.
    if len(text) > 180:
        return None
    return "YUANBAO_CONTENT_UNAVAILABLE" if any(p.fullmatch(text) for p in _REFUSAL_PATTERNS) else None

def valid_analysis(value: str | None) -> bool:
    return refusal_reason(value) is None
