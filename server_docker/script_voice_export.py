"""Export a Yuanbao-edited spoken script, never mislabel it as verbatim ASR."""
from __future__ import annotations

import re
from pathlib import Path


def wants_script_export(preset: dict | None, prompt: str | None) -> bool:
    if isinstance(preset, dict) and preset.get('id') == 'script_copy':
        return True
    text = str(prompt or '')
    return '口播脚本' in text and ('提炼' in text or '字幕' in text or '完整' in text)


def extract_spoken_script(content: str) -> tuple[str, bool]:
    lines = str(content or '').splitlines()
    start = None
    prefix = re.compile(r'^\s*(?:#{1,4}\s*)?(?:[一二三四五六七八九十]+[、.．]\s*|\d+[.、]\s*)?')
    for i, line in enumerate(lines):
        heading = prefix.sub('', line, count=1).strip()
        if len(heading) <= 48 and re.match(r'^(?:完整|视频)?口播脚本(?:[（(：:：\s]|$)', heading):
            start = i + 1
            break
    if start is None:
        return str(content or '').strip(), False
    stop = re.compile(r'^\s*(?:#{1,3}\s+|[一二三四五六七八九十]+[、.．]\s*)')
    end = next((i for i in range(start, len(lines)) if stop.match(lines[i])), len(lines))
    excerpt = '\n'.join(lines[start:end]).strip()
    return (excerpt, True) if excerpt else (str(content or '').strip(), False)


def write_script_voice(download_dir: Path, job_id: str, video_title: str, content: str, *, output_format: str = "txt") -> str:
    """Creates a download tied to the job owner via transcript_filename."""
    if not re.fullmatch(r'wxv_\d{6}', str(job_id)):
        raise ValueError('invalid job id')
    script, extracted = extract_spoken_script(content)
    if not script:
        raise ValueError('empty script')
    output = Path(download_dir)
    output.mkdir(parents=True, exist_ok=True)
    if output_format not in {'md', 'txt'}:
        raise ValueError('unsupported format')
    path = output / (job_id + ('.narration-ai.' + output_format if output_format == 'md' else '.narration-ai.txt'))
    note = ('元宝 AI 整理版口播脚本（非音频逐字转写；可能删减或改写原句）'
            if extracted else '元宝 AI 分析内容（未识别到独立口播脚本段落；非逐字稿，完整性未经验证）')
    payload = f'{note}\n视频：{video_title or "微信视频号"}\n任务：{job_id}\n\n{script}\n'
    path.write_text(payload, encoding='utf-8')
    return path.name
