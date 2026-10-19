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
    # Yuanbao sometimes responds with a *long* generic knowledge note after
    # explicitly admitting it never read the video. Inspect only its opening;
    # do not treat a genuine discussion that later quotes an error as refusal.
    lead = text[:420]
    if (re.search(r"^(?:我这边|我尝试|抱歉|目前|当前).{0,170}(?:视频解析失败|无法解析|无法读取|视频不可读)", lead, re.S)
            and re.search(r"(?:没有拿到|未能获取|无法取得|没有获取|兜底|补发视频)", lead)):
        return "YUANBAO_VIDEO_NOT_READ"
    if len(text) > 180:
        return None
    return "YUANBAO_CONTENT_UNAVAILABLE" if any(p.fullmatch(text) for p in _REFUSAL_PATTERNS) else None

def valid_analysis(value: str | None) -> bool:
    return refusal_reason(value) is None
