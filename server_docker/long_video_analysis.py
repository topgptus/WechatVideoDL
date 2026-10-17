"""Ground long-video AI notes in local ASR; preserve every source chunk."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Callable

# The Yuanbao browser function accepts at most 14k characters of transcript.
MAX_SOURCE_CHARS = 8000
MAX_SOURCE_CHUNKS = 48


def split_asr(text: str, max_chars: int = MAX_SOURCE_CHARS) -> list[str]:
    lines = text.splitlines()
    if not lines or not text.strip():
        return []
    chunks: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        # Preserve real window/timecode boundaries whenever possible.
        while len(line) > max_chars:
            if current:
                chunks.append("\n".join(current)); current = []; size = 0
            chunks.append(line[:max_chars]);line = line[max_chars:]
        cost = len(line) + 1
        if current and size + cost > max_chars:
            chunks.append("\n".join(current));current = [];size = 0
        if line:
            current.append(line);size += cost
    if current:
        chunks.append("\n".join(current))
    return chunks


def collect_chunks(transcript: str, goal: str, ask: Callable[[str, str], str],
                   *, progress: Callable[[int, int], None] | None = None,
                   checkpoint: Path | None = None) -> dict:
    chunks = split_asr(transcript)
    if not chunks or len(chunks) > MAX_SOURCE_CHUNKS:
        raise ValueError("TRANSCRIPT_TOO_LONG")
    notes: list[str] = []
    edited: list[str] = []
    source_sha = hashlib.sha256((transcript + "\n" + goal).encode()).hexdigest()
    checkpoint = Path(checkpoint) if checkpoint is not None else None
    if checkpoint and checkpoint.is_file() and not checkpoint.is_symlink():
        previous = json.loads(checkpoint.read_text("utf-8"))
        if previous.get("source_sha") != source_sha or previous.get("total") != len(chunks):
            raise ValueError("AI_CHECKPOINT_SOURCE_MISMATCH")
        notes.extend(previous.get("notes", []))
        edited.extend(previous.get("edited", []))
        if len(notes) != len(edited) or len(notes) > len(chunks):
            raise ValueError("AI_CHECKPOINT_INVALID")
    for index, chunk in enumerate(chunks, 1):
        if index <= len(notes):
            continue
        instruction = (
            f"这是真实本地 SenseVoice 逐字稿的第 {index}/{len(chunks)} 部分。"
            f"用户要求：{goal}\n"
            "请只根据这段逐字稿，输出两节：\n"
            "【本段要点】按主题提炼关键观点和具体原话，必要时引用可见时间轴；\n"
            "【本段口播整理】按原有顺序整理口播文案，删去重复但保留事实，不添加原文中没有的观点。\n"
            "如果语音识别内容不明，请注明听辨不清，禁止编造。"
        )
        response = str(ask(chunk, instruction) or "").strip()
        if len(response) < 70 or any(x in response[:230] for x in
            ("无法读取视频", "没有提供逐字稿", "无法处理逐字稿", "视频解析失败")):
            raise ValueError(f"AI_CHUNK_UNRELIABLE_{index}")
        # Require the requested source-grounded structure before marking a chunk complete.
        if "【本段要点】" not in response or "【本段口播整理】" not in response:
            raise ValueError(f"AI_CHUNK_STRUCTURE_MISSING_{index}")
        head, tail = response.split("【本段口播整理】", 1)
        notes.append(head.split("【本段要点】",1)[-1].strip())
        edited.append(tail.strip())
        if checkpoint is not None:
            checkpoint.parent.mkdir(parents=True, exist_ok=True)
            temporary = checkpoint.with_name("." + checkpoint.name + ".tmp")
            temporary.write_text(json.dumps({"source_sha":source_sha,"total":len(chunks),
                "notes":notes,"edited":edited}, ensure_ascii=False), encoding="utf8")
            os.chmod(temporary, 0o600)
            os.replace(temporary, checkpoint)
        if progress:
            progress(index, len(chunks))
    return {"notes": notes, "edited": edited, "chunk_count": len(chunks),
            "source_chars": len(transcript)}


def assemble_grounded_analysis(grouped: dict, goal: str,
                               ask: Callable[[str,str],str]) -> tuple[str,str]:
    """Produce notes plus an edited narration; no 'complete verbatim' claims."""
    sections = [f"【第 {i}/{len(grouped['notes'])} 部分要点】\n{x}"
                for i,x in enumerate(grouped['notes'],1)]
    combined = "\n\n".join(sections)
    # For large transcripts, carry compact evidence forward hierarchically.
    chunks = split_asr(combined, max_chars=11000)
    if len(chunks) > 1:
        reduced = []
        for i,x in enumerate(chunks,1):
            reduced.append(ask(x, f"合并下列第 {i}/{len(chunks)} 组有来源的口播要点，"
                        "保留关键事实、原话及时间标记，不引入未提供的信息。"))
        combined = "\n\n".join(reduced)
    if len(combined) > 12000:
        raise ValueError("AI_EVIDENCE_TOO_LARGE")
    synthesis = ask(combined,
        f"根据以下来源于实际 SenseVoice 口播识别的分段要点，完成用户要求：{goal}\n"
        "写出全面而可核对的结论、按主题归类关键观点和原话，"
        "不得声称你自行看过视频；不确定处需要说明。")
    if len(synthesis.strip()) < 70:
        raise ValueError("AI_SYNTHESIS_EMPTY")
    intro = ("# 基于本地语音识别的 AI 解析\n\n"
             "来源：SenseVoice 原始逐字稿；AI 只负责整理，可能存在转写或概括误差。\n\n")
    analysis = (intro + synthesis.strip() + "\n\n## 分段证据与要点\n\n"
                + "\n\n".join(f"### 第 {i} 部分\n{note}" for i,note in enumerate(grouped['notes'],1)))
    narration = ("二、完整口播脚本（整理版）\n"
                 + "（本文件按全部来源部分整理；不是未经修改的逐字记录。）\n\n"
                 + "\n\n".join(f"第 {i}/{len(grouped['edited'])} 部分：\n{x}"
                                 for i,x in enumerate(grouped['edited'],1)))
    return analysis, narration
