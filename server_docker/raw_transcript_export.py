"""Produce Markdown/TXT from SenseVoice's actual time windows; no text rewriting."""
from __future__ import annotations
import re
from pathlib import Path

STAMP = re.compile(r'^\[(\d{2}:\d{2}:\d{2}\.\d{3})\s*[-–]\s*(\d{2}:\d{2}:\d{2}\.\d{3})\]\s*(.*)$')


def readable_paragraphs(lines: list[str], *, target_chars: int = 190,
                        max_chars: int = 330) -> str:
    """Reflow ASR windows into paragraphs without changing recognized characters.

    Use punctuation *already produced by ASR*; never guess speakers, insert
    words/punctuation, or call AI. Timestamped export retains each window.
    """
    combined = "".join(lines)
    if not combined:
        return ""
    # Keep sentence punctuation with the sentence; prefer ending a paragraph
    # at a real period/question mark, not an arbitrary 19-second boundary.
    clauses = re.findall(r"[^。！？!?；;，,]*[。！？!?；;，,]*|[^。！？!?；;，,]+$", combined)
    clauses = [x for x in clauses if x]
    paragraphs: list[str] = []
    current = ""
    for clause in clauses:
        if (current and len(current) >= target_chars
                and len(current) + len(clause) > max_chars):
            paragraphs.append(current)
            current = ""
        current += clause
        # End at a strong sentence mark once the paragraph has enough body.
        if len(current) >= target_chars and re.search(r"[。！？!?；;][”’\"']?$", clause):
            paragraphs.append(current)
            current = ""
    if current:
        paragraphs.append(current)
    # Content must survive the formatting pass byte-for-byte, ignoring only
    # paragraph separators intentionally introduced by this function.
    assert "".join(paragraphs) == combined
    return "\n\n".join(paragraphs)


def write_raw_artifacts(download_dir: Path, job_id: str, transcript: str,
                        output_format: str, with_timing: bool) -> tuple[str | None, str | None]:
    """Exactly one raw ASR artifact per task: plain OR timestamped."""
    if not re.fullmatch(r'wxv_\d{6}', job_id) or output_format not in ('md', 'txt'):
        raise ValueError('invalid raw transcript options')
    source_lines = [x.strip() for x in transcript.splitlines() if x.strip()]
    if not source_lines:
        raise ValueError('empty transcript')
    parsed = [STAMP.fullmatch(line) for line in source_lines]
    # Validate before creating any files; a failed timestamped request must
    # not leave an unrequested plain transcript behind.
    if with_timing and not all(parsed):
        raise ValueError('TIMESTAMPS_UNAVAILABLE')
    root = Path(download_dir)
    root.mkdir(parents=True, exist_ok=True)
    heading = ('# ' if output_format == 'md' else '')
    notice = ('由 SenseVoice 识别，未经 AI 文案改写；可能存在识别误差。\n\n'
              '仅按已有标点整理换行，不修改识别词语；自然段不对应固定时长。\n\n')
    if with_timing:
        timed_name = f'{job_id}.transcript-timed.{output_format}'
        rows = [f'[{m.group(1)}–{m.group(2)}] {m.group(3)}' for m in parsed]
        (root / timed_name).write_text(
            heading + '带时间轴的语音识别逐字稿\n\n' + notice
            + '\n'.join(rows) + '\n', encoding='utf-8')
        return None, timed_name

    raw_name = f'{job_id}.transcript-raw.{output_format}'
    text_lines = [m.group(3) if m else line for m, line in zip(parsed, source_lines)]
    (root / raw_name).write_text(
        heading + '原始语音识别逐字稿\n\n' + notice
        + readable_paragraphs(text_lines) + '\n', encoding='utf-8')
    return raw_name, None
