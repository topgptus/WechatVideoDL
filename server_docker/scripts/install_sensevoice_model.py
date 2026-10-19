#!/usr/bin/env python3
"""Opt-in model installer for the official sherpa-onnx SenseVoice INT8 archive.

Runs on the HOST as an explicitly invoked admin action. Nothing is downloaded
by the web app or by a running ASR worker. The archive is never extractall'ed.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import tarfile
import tempfile
from urllib.parse import urlparse
from urllib.request import build_opener, HTTPRedirectHandler, Request

MODEL_RELEASE = "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17"
OFFICIAL_ARCHIVE = (
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/"
    + MODEL_RELEASE + ".tar.bz2"
)
ALLOWED = {"model.int8.onnx": (100_000_000, 400_000_000),
           "tokens.txt": (1_000, 5_000_000),
           "LICENSE": (0, 100_000),
           "README.md": (0, 100_000)}
MAX_ARCHIVE_BYTES = 650 * 1024 * 1024


def _valid_redirect(url: str) -> bool:
    parts = urlparse(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and (
        host == "github.com" or host == "release-assets.githubusercontent.com"
    )


class TrustedRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _valid_redirect(newurl):
            raise ValueError("UNTRUSTED_MODEL_REDIRECT")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_archive(dest: Path, *, expected_sha256: str | None = None) -> str:
    digest = hashlib.sha256()
    written = 0
    opener = build_opener(TrustedRedirects)
    with opener.open(Request(OFFICIAL_ARCHIVE), timeout=50) as response:
        if not _valid_redirect(response.geturl()):
            raise ValueError("UNTRUSTED_MODEL_SOURCE")
        with dest.open("xb") as output:
            os.chmod(dest, 0o600)
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                written += len(block)
                if written > MAX_ARCHIVE_BYTES:
                    raise ValueError("MODEL_ARCHIVE_TOO_LARGE")
                digest.update(block)
                output.write(block)
    if not written:
        raise ValueError("EMPTY_MODEL_ARCHIVE")
    sha = digest.hexdigest()
    if expected_sha256 and sha != expected_sha256.lower():
        raise ValueError("MODEL_SHA256_MISMATCH")
    return sha


def install_model(target: Path, archive: Path, *, expected_sha256: str | None = None) -> bool:
    """Safely install two necessary files plus their license; never overwrite data."""
    target = target.expanduser().absolute()
    if target.is_symlink():
        raise ValueError("MODEL_TARGET_IS_SYMLINK")
    if target.exists():
        if (target.is_dir() and not target.is_symlink()
                and (target / "model.int8.onnx").is_file()
                and (target / "tokens.txt").is_file()
                and not (target / "model.int8.onnx").is_symlink()
                and not (target / "tokens.txt").is_symlink()):
            return False
        raise ValueError("MODEL_TARGET_ALREADY_EXISTS")
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.parent.is_symlink():
        raise ValueError("MODEL_PARENT_IS_SYMLINK")
    if expected_sha256:
        sha = hashlib.sha256()
        with archive.open("rb") as f:
            for block in iter(lambda: f.read(1024 * 1024), b""):
                sha.update(block)
        if sha.hexdigest() != expected_sha256.lower():
            raise ValueError("MODEL_SHA256_MISMATCH")
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("MODEL_ARCHIVE_TOO_LARGE")
    with tempfile.TemporaryDirectory(prefix=".sensevoice-install-", dir=target.parent) as folder:
        staging = Path(folder) / "sensevoice"
        staging.mkdir(mode=0o700)
        seen: set[str] = set()
        with tarfile.open(archive, mode="r:bz2") as tar:
            count = 0
            for member in tar:
                count += 1
                if count > 200:
                    raise ValueError("TOO_MANY_MODEL_MEMBERS")
                if not member.isfile():
                    continue
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    raise ValueError("UNSAFE_MODEL_ARCHIVE")
                pieces = Path(member.name).parts
                if len(pieces) != 2 or pieces[0] != MODEL_RELEASE:
                    continue
                filename = pieces[1]
                if filename not in ALLOWED:
                    continue
                minimum, maximum = ALLOWED[filename]
                if filename in seen or not minimum <= member.size <= maximum:
                    raise ValueError("INVALID_MODEL_MEMBER")
                seen.add(filename)
                stream = tar.extractfile(member)
                if stream is None:
                    raise ValueError("INVALID_MODEL_MEMBER")
                dest = staging / filename
                remaining = member.size
                with dest.open("xb") as output:
                    while remaining:
                        block = stream.read(min(1024 * 1024, remaining))
                        if not block:
                            raise ValueError("TRUNCATED_MODEL_MEMBER")
                        output.write(block)
                        remaining -= len(block)
                os.chmod(dest, 0o644)
        if not {"model.int8.onnx", "tokens.txt", "LICENSE"} <= seen:
            raise ValueError("INCOMPLETE_MODEL_ARCHIVE")
        os.chmod(staging, 0o755)
        os.rename(staging, target)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Install the official optional offline SenseVoice model.")
    parser.add_argument("--target", type=Path, default=Path("models/sensevoice"))
    parser.add_argument("--archive", type=Path, help="Use an already downloaded archive (offline installations)")
    parser.add_argument("--sha256", help="Optional trusted SHA256 of the official archive")
    args = parser.parse_args()
    if args.sha256 and (len(args.sha256) != 64 or any(c not in "0123456789abcdefABCDEF" for c in args.sha256)):
        parser.error("--sha256 must be exactly 64 hex characters")
    dest = args.target.expanduser().absolute()
    if dest.exists() and not dest.is_symlink() and (dest / "model.int8.onnx").is_file():
        print("SenseVoice model already installed; no changes")
        return
    if args.archive:
        changed = install_model(dest, args.archive, expected_sha256=args.sha256)
    else:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".sensevoice-download-", dir=dest.parent) as folder:
            archive = Path(folder) / "model.tar.bz2"
            digest = download_archive(archive, expected_sha256=args.sha256)
            print("Official model archive SHA256:", digest)
            changed = install_model(dest, archive)
    print("SenseVoice model installed" if changed else "SenseVoice model already present")


if __name__ == "__main__":
    main()
