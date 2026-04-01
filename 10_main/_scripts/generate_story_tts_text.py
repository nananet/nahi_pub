#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Story markdown から朗読用テキストを生成する。

- 入力: 10_main/story/*.md
- 出力: 10_main/story/_tts/<md の stem と同じフォルダ名>/<section>.txt
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STORY_DIR = ROOT / "story"
TTS_DIR = STORY_DIR / "_tts"

H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)
MD_STEM_ORDER_RE = re.compile(r"^(\d{2})_(.+)$")
RUBY_RE = re.compile(r"<ruby>(.*?)<rt>(.*?)</rt></ruby>", re.S)
SPAN_RE = re.compile(r"</?span[^>]*>", re.I)
TAG_RE = re.compile(r"<[^>]+>")


def sanitize_filename(name: str) -> str:
    out = name.strip()
    for c in '\\/:*?"<>|':
        out = out.replace(c, "_")
    return out or "section"


def normalize_tts_text(text: str) -> str:
    s = text
    # ruby は読みを優先
    s = RUBY_RE.sub(lambda m: m.group(2).strip(), s)
    s = SPAN_RE.sub("", s)
    s = TAG_RE.sub("", s)
    # blockquote の記号を除去
    s = re.sub(r"^\s*>\s?", "", s, flags=re.M)
    # Markdown の強調/リンクなどを最低限除去
    s = re.sub(r"\[(.*?)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"`([^`]+)`", r"\1", s)
    s = re.sub(r"^\s*[-*]\s+", "", s, flags=re.M)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip() + "\n"


def split_h2_sections(md: str) -> list[tuple[str, str]]:
    matches = list(H2_RE.finditer(md))
    if not matches:
        return []
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(md)
        body = md[start:end].strip()
        out.append((title, body))
    return out


def generate_for_story(md_path: Path) -> list[Path]:
    text = md_path.read_text(encoding="utf-8")
    sections = split_h2_sections(text)
    if not sections:
        return []

    story_name = sanitize_filename(md_path.stem)
    out_base = TTS_DIR / story_name
    out_base.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for sec_title, sec_body in sections:
        # 解説は朗読対象外
        if sec_title.startswith("解説"):
            continue
        sec_name = sanitize_filename(sec_title)
        out_path = out_base / f"{sec_name}.txt"
        out_path.write_text(normalize_tts_text(sec_body), encoding="utf-8")
        written.append(out_path)

    # 連結版
    if written:
        merged = out_base / "_all.txt"
        parts: list[str] = []
        for p in written:
            parts.append(f"【{p.stem}】\n")
            parts.append(p.read_text(encoding="utf-8").rstrip() + "\n\n")
        merged.write_text("".join(parts).rstrip() + "\n", encoding="utf-8")
        written.append(merged)
    return written


def main() -> None:
    TTS_DIR.mkdir(parents=True, exist_ok=True)
    targets = [p for p in STORY_DIR.glob("*.md") if p.name != "_index.md"]

    def sort_key(p: Path) -> tuple[int, str]:
        stem = sanitize_filename(p.stem)
        m = MD_STEM_ORDER_RE.match(stem)
        if m:
            return (int(m.group(1)), m.group(2))
        return (99_99, stem)

    targets.sort(key=sort_key)
    count = 0
    for md in targets:
        outs = generate_for_story(md)
        if outs:
            count += len(outs)
    print(f"OK: generated {count} tts files under {TTS_DIR}")


if __name__ == "__main__":
    main()

