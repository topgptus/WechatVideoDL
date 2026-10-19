#!/usr/bin/env python3
"""Offline SenseVoice INT8 ASR. Audio stays local; no model download.

Called by run.py through a private isolated sherpa-onnx Python interpreter.
Writes per-window checkpoint files (SHA validated) and transcript/SRT/manifest.
"""
from __future__ import annotations
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import wave

WINDOW_S = 20
HOP_S = 19
MAX_AUDIO_S = 7200
MAX_PARTS = 400


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fail(code: str) -> None:
    raise ValueError(code)


def ffprobe(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=15)
    if r.returncode:
        fail("INVALID_AUDIO")
    seconds = float(r.stdout.strip())
    if not 0.5 < seconds <= MAX_AUDIO_S:
        fail("AUDIO_TOO_LONG_OR_INVALID")
    return seconds


def safe_write_json(path: Path, payload: dict) -> None:
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=".asr-", suffix=".tmp",
        encoding="utf-8", delete=False,
    ) as f:
        tmp = Path(f.name)
        os.chmod(tmp, 0o600)
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, path)


def safe_write_text(path: Path, value: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w", dir=path.parent, prefix=".asr-", suffix=".tmp",
        encoding="utf-8", delete=False,
    ) as f:
        tmp = Path(f.name)
        os.chmod(tmp, 0o600)
        f.write(value)
    os.replace(tmp, path)


def fmt(seconds: float, subtitle: bool = False) -> str:
    n = max(0, round(seconds * 1000))
    hour, n = divmod(n, 3600000)
    minute, n = divmod(n, 60000)
    sec, ms = divmod(n, 1000)
    return f"{hour:02}:{minute:02}:{sec:02}{',' if subtitle else '.'}{ms:03}"


def quality_flags(text: str, seconds: float, token_times: list[float]) -> list[str]:
    out = []
    if len(text.strip()) < 6 and seconds > 10:
        out.append("LOW_TEXT_COVERAGE")
    if re.search(r"(.)\1{12,}", text):
        out.append("REPEATED_CHARACTER_HALLUCINATION")
    if len(text) >= 35 and max(text.count(ch) for ch in set(text)) > len(text) * .5:
        out.append("LOW_TEXT_DIVERSITY")
    if token_times and seconds > 15 and token_times[-1] < seconds - 7:
        out.append("EARLY_END_LAST_TOKEN")
    return out


def make_srt(events: list[tuple[float, str]], duration: float) -> str:
    if not events:
        return ""
    captions = []
    chunk = []
    started = events[0][0]
    for stamp, token in events:
        if not chunk:
            started = stamp
        chunk.append((stamp, token))
        end_line = (token.endswith(("。", "！", "？", "，", "；", "："))
                    or len(chunk) >= 28 or stamp - started >= 5.0)
        if end_line:
            text = "".join(t for _t, t in chunk).strip()
            if text:
                begin = max(0.0, started - .15)
                end = min(duration, max(stamp + .35, begin + .40))
                captions.append((begin, end, text))
            chunk = []
    if chunk:
        stamp = chunk[-1][0]
        text = "".join(x for _, x in chunk).strip()
        if text:
            begin = max(0, started - .15)
            end = min(duration, max(stamp + .35, begin + .30))
            captions.append((begin, end, text))
    return "\n".join(
        f"{i}\n{fmt(a,True)} --> {fmt(b,True)}\n{txt}\n"
        for i, (a, b, txt) in enumerate(captions, 1)
    )


def main() -> dict:
    req = json.loads(sys.stdin.read(20000))
    src = Path(req["input"]).expanduser()
    out = Path(req["output_dir"]).expanduser()
    model_dir = Path(req["model_dir"]).expanduser()
    if (src.is_symlink() or not src.is_file() or
        src.suffix.lower() not in {".wav", ".mp3", ".m4a", ".flac"}):
        fail("INVALID_AUDIO")
    if not out.is_dir() or out.is_symlink() or not model_dir.is_dir() or model_dir.is_symlink():
        fail("INVALID_DEST_OR_MODEL")
    onnx = model_dir / "model.int8.onnx"
    tokens_file = model_dir / "tokens.txt"
    if (not onnx.is_file() or onnx.is_symlink() or
        not tokens_file.is_file() or tokens_file.is_symlink()):
        fail("MODEL_NOT_CONFIGURED")
    transcript = out / "transcript.sensevoice.txt"
    raw_output = out / "transcript.sensevoice.raw.txt"
    srt = out / "transcript.sensevoice.srt"
    manifest = out / "transcript.sensevoice.manifest.json"
    if any(p.exists() or p.is_symlink() for p in (transcript, raw_output, srt, manifest)):
        fail("OUTPUT_EXISTS")
    seconds = ffprobe(src)
    sha = digest(src)
    mhash = digest(onnx)
    folder = out / "sensevoice_parts"
    if folder.exists():
        if folder.is_symlink() or not folder.is_dir():
            fail("UNSAFE_PARTS_DIRECTORY")
    else:
        folder.mkdir(mode=0o700)
    starts = list(range(0, math.ceil(seconds), HOP_S))
    if len(starts) > MAX_PARTS:
        fail("TOO_MANY_PARTS")
    import numpy as np
    import sherpa_onnx
    recog = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=str(onnx), tokens=str(tokens_file), num_threads=4,
        language="zh", use_itn=True, provider="cpu")
    started = time.monotonic()
    parts = []
    warnings = []
    for i, offset in enumerate(starts, 1):
        checkpoint = folder / f"part_{i:03}.json"
        expected = {"index": i, "offset": offset, "source_sha256": sha,
                    "model_sha256": mhash, "window_seconds": WINDOW_S,
                    "hop_seconds": HOP_S}
        if checkpoint.exists():
            if checkpoint.is_symlink() or not checkpoint.is_file():
                fail("UNSAFE_CHECKPOINT")
            rec = json.loads(checkpoint.read_text("utf8"))
            if any(rec.get(k) != v for k, v in expected.items()):
                fail("CHECKPOINT_MISMATCH")
        else:
            wav = folder / f".part_{i:03}.wav"
            if wav.exists() or wav.is_symlink():
                fail("STALE_TEMP_AUDIO")
            length = min(WINDOW_S, seconds - offset)
            p = subprocess.run([
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-n", "-ss", str(offset), "-i", str(src),
                "-t", str(length), "-vn", "-ar", "16000", "-ac", "1",
                "-c:a", "pcm_s16le", str(wav)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
            if p.returncode:
                fail("AUDIO_SLICE_FAILED")
            os.chmod(wav, 0o600)
            try:
                with wave.open(str(wav), "rb") as f:
                    if (f.getnchannels(), f.getsampwidth(), f.getframerate()) != (1, 2, 16000):
                        fail("UNEXPECTED_SAMPLE_FORMAT")
                    pcm = f.readframes(f.getnframes())
                samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
                stream = recog.create_stream()
                stream.accept_waveform(16000, samples)
                t = time.monotonic()
                recog.decode_stream(stream)
                result = stream.result
                result_text = str(result.text)
                token_list = list(result.tokens)
                times = [float(x) for x in result.timestamps]
                flags = quality_flags(result_text, len(samples)/16000, times)
                if len(token_list) != len(times):
                    flags.append("TOKEN_TIME_MISMATCH")
                rec = {**expected, "duration_s": round(len(samples)/16000, 3),
                       "text": result_text, "tokens": token_list,
                       "timestamps": times, "warnings": flags,
                       "decode_seconds": round(time.monotonic()-t, 3)}
                safe_write_json(checkpoint, rec)
            finally:
                wav.unlink(missing_ok=True)
        parts.append(rec)
        if rec.get("warnings"):
            warnings.append({"part": i, "codes": rec["warnings"]})

    raw = "\n".join(
        f"[{fmt(x['offset'])} - {fmt(x['offset']+x['duration_s'])}] {x['text']}"
        for x in parts) + "\n"
    merged = []
    timeline = []
    for i, data in enumerate(parts):
        start = float(data["offset"])
        duration = float(data["duration_s"])
        begin = start + (.5 if i > 0 else 0.0)
        end = min(seconds, start + duration - (.5 if i < len(parts)-1 else 0.0))
        tokens = data["tokens"]
        times = data["timestamps"]
        if len(tokens) != len(times):
            warnings.append({"part": i+1, "codes": ["MERGE_TIME_MISMATCH"]})
            text = data["text"]
        else:
            kept = [(start+ts, str(tok)) for tok, ts in zip(tokens, times)
                    if begin <= start+ts < end]
            text = "".join(tok for _, tok in kept)
            timeline.extend(kept)
        merged.append(f"[{fmt(begin)} - {fmt(end)}] {text}")
    stitched = "\n".join(merged) + "\n"
    captions = make_srt(timeline, seconds)
    # All window checkpoints must be valid before publishing final files.
    safe_write_text(raw_output, raw)
    safe_write_text(transcript, stitched)
    safe_write_text(srt, captions)
    summary = {
        "format": 1, "model": "sensevoice-int8-2024",
        "source_sha256": sha, "model_sha256": mhash,
        "duration_seconds": round(seconds, 3),
        "parts": len(parts), "window_seconds": WINDOW_S, "hop_seconds": HOP_S,
        "recognized_chars_raw": sum(len(x["text"]) for x in parts),
        "recognized_chars_merged": sum(len(x.split("] ", 1)[-1]) for x in merged),
        "warning_count": len(warnings), "warnings": warnings,
        "quality_status": "needs_review" if warnings else "unverified",
        "decoder_seconds": round(sum(p.get("decode_seconds", 0) for p in parts), 2),
        "wall_seconds": round(time.monotonic()-started, 2),
        "uploaded": False,
    }
    safe_write_json(manifest, summary)
    return {"ok": True, "skill_action": "transcribe_sensevoice",
            "engine": "sherpa-onnx SenseVoice INT8",
            "parts": len(parts), "duration_seconds": round(seconds, 3),
            "characters": summary["recognized_chars_merged"],
            "warning_count": len(warnings), "quality_status": summary["quality_status"],
            "transcript_file": str(transcript), "raw_file": str(raw_output),
            "srt_file": str(srt), "manifest_file": str(manifest)}


if __name__ == "__main__":
    try:
        print(json.dumps(main(), ensure_ascii=False))
    except Exception as exc:
        # Keep stdout machine-readable; never expose private audio or model paths.
        code = str(exc) if isinstance(exc, ValueError) else "ENGINE_FAILED"
        if code not in {
            "INVALID_AUDIO", "INVALID_DEST_OR_MODEL", "MODEL_NOT_CONFIGURED",
            "OUTPUT_EXISTS", "UNSAFE_PARTS_DIRECTORY", "UNSAFE_CHECKPOINT",
            "CHECKPOINT_MISMATCH", "STALE_TEMP_AUDIO", "AUDIO_SLICE_FAILED",
            "UNEXPECTED_SAMPLE_FORMAT", "TOO_MANY_PARTS", "AUDIO_TOO_LONG_OR_INVALID",
            "ENGINE_FAILED",
        }:
            code = "ENGINE_FAILED"
        print(json.dumps({"ok": False, "code": code}, ensure_ascii=False))
        raise SystemExit(2)
