#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
story の Markdown（ルビ付き本文）を、ブルーアーカイブ風ノベルUIの動画にする。

- 原稿は .txt ではなく .md の実表記（<ruby>…<rt>…</rt>）→ ルビを上に表示
- 折り返しはパネル内ピクセル幅で貪欲に行い、空行区切りの段落は別ブロックとしてレイアウト
- 既定で A.I.VOICE 出力 WAV（01_Arcaea など）を長さに合わせて合成

依存:
  pip install pillow numpy moviepy

例:
  python render_novel_video_ba_style.py
  python render_novel_video_ba_style.py --sections 0-1
  python render_novel_video_ba_style.py --sections 0-1,0-2,0-3 --audio path/to.wav
"""

from __future__ import annotations

import argparse
import re
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
STORY_DIR = ROOT / "story"
DEFAULT_MD = STORY_DIR / "01_Arcaea.md"
DEFAULT_AUDIO = STORY_DIR / "_audio" / "01_Arcaea" / "0-1_to_0-3_aivoice.wav"
DEFAULT_OUT = STORY_DIR / "_video" / "01_Arcaea_novel_ba_exp.mp4"

RUBY_RE = re.compile(r"<ruby>(.*?)<rt>(.*?)</rt></ruby>", re.DOTALL)
FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
H2_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)

# ブルーアーカイブ寄せ
COL_BG_TOP = (45, 58, 92)
COL_BG_BOT = (18, 28, 46)
COL_PANEL = (22, 32, 58)
COL_PANEL_EDGE = (94, 180, 255)
COL_ACCENT_LINE = (94, 220, 255)
COL_TEXT = (232, 244, 255)
COL_RUBY = (160, 210, 255)
COL_NAME_BG = (30, 55, 90)
COL_NAME_TEXT = (180, 230, 255)

Unit = tuple  # ("c", ch) | ("r", base, rt)


def _pick_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        Path(r"C:\Windows\Fonts\Yugothic.ttf"),
        Path(r"C:\Windows\Fonts\YuGothM.ttc"),
        Path(r"C:\Windows\Fonts\meiryo.ttc"),
        Path(r"C:\Windows\Fonts\msgothic.ttc"),
    ]
    for p in candidates:
        if p.is_file():
            try:
                return ImageFont.truetype(str(p), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> float:
    if not text:
        return 0.0
    if hasattr(font, "getlength"):
        return float(font.getlength(text))
    bbox = draw.textbbox((0, 0), text, font=font)
    return float(bbox[2] - bbox[0])


def _strip_frontmatter(md: str) -> str:
    return FRONTMATTER_RE.sub("", md, count=1).lstrip("\n")


def _clean_md_fragment(s: str) -> str:
    s = re.sub(r"<rp>.*?</rp>", "", s, flags=re.I | re.DOTALL)
    s = re.sub(r"<span[^>]*>", "", s, flags=re.I)
    s = re.sub(r"</span>", "", s, flags=re.I)
    lines = []
    for ln in s.splitlines():
        ln = re.sub(r"^\s*>\s?", "", ln)
        lines.append(ln)
    return "\n".join(lines)


def parse_md_sections(md_path: Path) -> dict[str, str]:
    raw = md_path.read_text(encoding="utf-8")
    body = _strip_frontmatter(raw)
    matches = list(H2_RE.finditer(body))
    out: dict[str, str] = {}
    for i, m in enumerate(matches):
        title = m.group(1).strip()
        if title.startswith("解説"):
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        out[title] = _clean_md_fragment(body[start:end].strip())
    return out


def tokenize_with_ruby(text: str) -> list[Unit]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    tokens: list[Unit] = []
    pos = 0
    for m in RUBY_RE.finditer(text):
        if m.start() > pos:
            tokens.extend(_plain_to_char_units(text[pos : m.start()]))
        base = re.sub(r"\s+", "", m.group(1).strip())
        rt = m.group(2).strip()
        if base:
            tokens.append(("r", base, rt))
        pos = m.end()
    if pos < len(text):
        tokens.extend(_plain_to_char_units(text[pos:]))
    return tokens


def _plain_to_char_units(s: str) -> list[Unit]:
    return [("c", ch) for ch in s]


def _unit_width(draw: ImageDraw.ImageDraw, u: Unit, font_b: ImageFont.ImageFont, font_r: ImageFont.ImageFont) -> float:
    if u[0] == "c":
        return _text_width(draw, u[1], font_b)
    _, base, rt = u
    wb = _text_width(draw, base, font_b)
    wr = _text_width(draw, rt, font_r)
    return max(wb, wr) + 6.0


def _layout_lines(
    units: list[Unit],
    draw: ImageDraw.ImageDraw,
    font_b: ImageFont.ImageFont,
    font_r: ImageFont.ImageFont,
    max_width: float,
) -> list[list[Unit]]:
    """ピクセル幅で貪欲折り返し。行末の句読点だけ次行に送らず 1 行に収める。"""
    q: deque[Unit] = deque(units)
    lines: list[list[Unit]] = []
    while q:
        line: list[Unit] = []
        w_acc = 0.0
        while q:
            u = q[0]
            uw = _unit_width(draw, u, font_b, font_r)
            if not line:
                q.popleft()
                line.append(u)
                w_acc = uw
                continue
            if w_acc + uw <= max_width:
                q.popleft()
                line.append(u)
                w_acc += uw
                continue
            break
        if line:
            lines.append(line)
    return [ln for ln in lines if ln]


def _layout_all_sections(
    sec_map: dict[str, str],
    section_ids: list[str],
    draw: ImageDraw.ImageDraw,
    font_b: ImageFont.ImageFont,
    font_r: ImageFont.ImageFont,
    max_width: float,
) -> list[list[Unit]]:
    all_lines: list[list[Unit]] = []
    for sid in section_ids:
        sid = sid.strip()
        if not sid:
            continue
        body = sec_map.get(sid)
        if body is None:
            raise SystemExit(f"見出し ## {sid} が {list(sec_map.keys())} にありません。")
        for para in body.split("\n\n"):
            para = para.strip()
            if not para:
                continue
            units = tokenize_with_ruby(para)
            if not units:
                continue
            all_lines.extend(_layout_lines(units, draw, font_b, font_r, max_width))
    return all_lines


def _paginate_lines(
    lines: list[list[Unit]], max_lines_per_page: int
) -> list[list[list[Unit]]]:
    pages: list[list[list[Unit]]] = []
    buf: list[list[Unit]] = []
    for ln in lines:
        if len(buf) >= max_lines_per_page:
            pages.append(buf)
            buf = []
        buf.append(ln)
    if buf:
        pages.append(buf)
    return pages


def _page_weight(page: list[list[Unit]]) -> int:
    n = 0
    for ln in page:
        for u in ln:
            if u[0] == "c":
                n += 1
            else:
                n += len(u[1]) + len(u[2])
    return max(n, 1)


def _gradient_bg(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h))
    px = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(COL_BG_TOP[0] * (1 - t) + COL_BG_BOT[0] * t)
        g = int(COL_BG_TOP[1] * (1 - t) + COL_BG_BOT[1] * t)
        b = int(COL_BG_TOP[2] * (1 - t) + COL_BG_BOT[2] * t)
        for x in range(w):
            px[x, y] = (r, g, b)
    return img


def _rounded_rect(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int, int, int],
    radius: int,
    fill: tuple[int, int, int],
    outline: tuple[int, int, int] | None = None,
    width: int = 2,
) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _draw_ruby_line(
    draw: ImageDraw.ImageDraw,
    x0: int,
    y0: int,
    line: list[Unit],
    font_b: ImageFont.ImageFont,
    font_r: ImageFont.ImageFont,
    ruby_h: int,
    gap: int,
) -> None:
    x = float(x0)
    y_rt = y0
    y_base = y0 + ruby_h + gap
    for u in line:
        if u[0] == "c":
            ch = u[1]
            if ch == "\n":
                continue
            draw.text((int(x), y_base), ch, font=font_b, fill=COL_TEXT)
            x += _text_width(draw, ch, font_b)
            continue
        _, base, rt = u
        wb = _text_width(draw, base, font_b)
        wr = _text_width(draw, rt, font_r)
        block = max(wb, wr) + 6.0
        draw.text((int(x + (block - wr) / 2), y_rt), rt, font=font_r, fill=COL_RUBY)
        draw.text((int(x + (block - wb) / 2), y_base), base, font=font_b, fill=COL_TEXT)
        x += block


def _draw_page_ruby(
    w: int,
    h: int,
    page_lines: list[list[Unit]],
    speaker: str,
    font_b: ImageFont.ImageFont,
    font_n: ImageFont.ImageFont,
    font_r: ImageFont.ImageFont,
    ruby_h: int,
) -> np.ndarray:
    img = _gradient_bg(w, h)
    draw = ImageDraw.Draw(img, "RGBA")

    margin_x = int(w * 0.055)
    panel_h = int(h * 0.36)
    panel_y = h - panel_h - int(h * 0.055)
    panel_x1 = margin_x
    panel_x2 = w - margin_x

    _rounded_rect(
        draw,
        (panel_x1, panel_y, panel_x2, h - int(h * 0.048)),
        radius=18,
        fill=COL_PANEL,
        outline=COL_PANEL_EDGE,
        width=2,
    )
    line_y = panel_y + 2
    draw.rectangle(
        (panel_x1 + 16, line_y, panel_x2 - 16, line_y + 4),
        fill=COL_ACCENT_LINE,
    )

    name_h = 36
    name_w = min(300, int(w * 0.3))
    name_x = panel_x1 + 22
    name_y = panel_y - name_h // 2
    _rounded_rect(
        draw,
        (name_x, name_y, name_x + name_w, name_y + name_h),
        radius=10,
        fill=COL_NAME_BG,
        outline=COL_PANEL_EDGE,
        width=1,
    )
    draw.text((name_x + 14, name_y + 8), speaker, font=font_n, fill=COL_NAME_TEXT)

    inner_left = panel_x1 + 26
    inner_right = panel_x2 - 26
    max_tw = inner_right - inner_left

    gap = 4
    line_step = ruby_h + gap + int(getattr(font_b, "size", 24)) + 10
    ty = panel_y + 22
    for ln in page_lines:
        if ty + line_step > h - int(h * 0.06):
            break
        _draw_ruby_line(draw, inner_left, ty, ln, font_b, font_r, ruby_h, gap)
        ty += line_step

    return np.asarray(img.convert("RGB"))


def _find_default_audio(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    wavs = sorted(folder.glob("*.wav"), key=lambda p: -p.stat().st_size)
    return wavs[0] if wavs else None


def _mpy_duration(clip, seconds: float):
    return clip.with_duration(seconds) if hasattr(clip, "with_duration") else clip.set_duration(seconds)


def _mpy_fps(clip, fps: int):
    return clip.with_fps(fps) if hasattr(clip, "with_fps") else clip.set_fps(fps)


def _mpy_subclip(clip, t0: float, t1: float):
    if hasattr(clip, "subclipped"):
        return clip.subclipped(t0, t1)
    return clip.subclip(t0, t1)


def _mpy_with_audio(clip, audio):
    return clip.with_audio(audio) if hasattr(clip, "with_audio") else clip.set_audio(audio)


def main() -> None:
    ap = argparse.ArgumentParser(description="Blue Archive–style novel UI from Markdown + ruby")
    ap.add_argument("--md", type=Path, default=DEFAULT_MD, help="ストーリー Markdown")
    ap.add_argument(
        "--sections",
        default="0-1,0-2,0-3",
        help="カンマ区切りの ## 見出し名（既定: 0-1〜0-3）",
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="出力 MP4")
    ap.add_argument("--speaker", default="Arcaea", help="ネームプレート")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument(
        "--seconds-per-page",
        type=float,
        default=0.0,
        help="音声なし時のみ有効。0 で固定 4 秒/ページ",
    )
    ap.add_argument("--max-pages", type=int, default=0, help="0=制限なし")
    ap.add_argument("--lines-per-page", type=int, default=4, help="1ページの最大行（ルビ行）")
    ap.add_argument("--audio", type=Path, default=None, help="合成する音声 WAV/MP3")
    ap.add_argument("--no-audio", action="store_true", help="音声を付けない")
    args = ap.parse_args()

    if not args.md.is_file():
        raise SystemExit(f"Markdown が見つかりません: {args.md}")

    try:
        from moviepy.editor import AudioFileClip, ImageClip, concatenate_videoclips
    except ImportError:
        try:
            from moviepy import AudioFileClip, ImageClip, concatenate_videoclips
        except ImportError as e:
            raise SystemExit("moviepy が必要です: pip install moviepy") from e

    section_ids = [s.strip() for s in args.sections.split(",") if s.strip()]
    sec_map = parse_md_sections(args.md)

    font_b = _pick_font(24)
    font_n = _pick_font(19)
    font_r = _pick_font(13)
    ruby_h = 15

    w, h = args.width, args.height
    margin_x = int(w * 0.055)
    panel_x1 = margin_x
    panel_x2 = w - margin_x
    inner_w = float(panel_x2 - panel_x1 - 52)

    dummy = Image.new("RGB", (10, 10))
    draw_m = ImageDraw.Draw(dummy)
    lines = _layout_all_sections(sec_map, section_ids, draw_m, font_b, font_r, inner_w)
    pages = _paginate_lines(lines, args.lines_per_page)
    if args.max_pages > 0:
        pages = pages[: args.max_pages]
    if not pages:
        raise SystemExit("ページが空です。")

    audio_path: Path | None = None
    if not args.no_audio:
        audio_path = args.audio
        if audio_path is not None and not audio_path.is_file():
            audio_path = None
        if audio_path is None and DEFAULT_AUDIO.is_file():
            audio_path = DEFAULT_AUDIO
        if audio_path is None:
            folder = STORY_DIR / "_audio" / args.md.stem
            audio_path = _find_default_audio(folder)

    weights = [_page_weight(p) for p in pages]
    total_w = sum(weights)

    if audio_path and audio_path.is_file():
        au_full = AudioFileClip(str(audio_path))
        total_dur = float(au_full.duration)
        page_durs = [total_dur * (wt / total_w) for wt in weights]
        au_full.close()
    else:
        per = args.seconds_per_page if args.seconds_per_page > 0 else 4.0
        page_durs = [per] * len(pages)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    clips: list = []
    for page, dur in zip(pages, page_durs):
        arr = _draw_page_ruby(w, h, page, args.speaker, font_b, font_n, font_r, ruby_h)
        ic = ImageClip(arr)
        ic = _mpy_duration(ic, max(dur, 0.3))
        ic = _mpy_fps(ic, args.fps)
        clips.append(ic)
    clip = concatenate_videoclips(clips, method="compose")

    if audio_path and audio_path.is_file():
        au = AudioFileClip(str(audio_path))
        dur = min(float(clip.duration), float(au.duration))
        clip = _mpy_subclip(clip, 0, dur)
        au_part = _mpy_subclip(au, 0, dur)
        clip = _mpy_with_audio(clip, au_part)
        au.close()
        clip.write_videofile(
            str(args.out),
            codec="libx264",
            audio_codec="aac",
            fps=args.fps,
            logger=None,
        )
    else:
        clip.write_videofile(
            str(args.out),
            codec="libx264",
            fps=args.fps,
            logger=None,
        )
    clip.close()
    print(f"OK: {args.out} (sections={section_ids}, audio={audio_path})")


if __name__ == "__main__":
    main()
