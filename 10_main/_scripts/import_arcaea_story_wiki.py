#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Arcaea Wiki（wikiwiki.jp）の Main Story 各ページ HTML から日本語本文を抽出し、
story/NN_<名前>.md を生成する（## 節 ＋ <ruby><rt> 形式。番号は story_pack_order.json）。

Wiki の本文は <p> 内に <br class="spacer"/> が連続する。BeautifulSoup の br.replace_with
で改行に置き換えるとツリーが壊れ本文が途中で切れるため、decode_contents 後に
<br…> を正規表現で改行へ変換する。

依存: pip install beautifulsoup4

例:
  python import_arcaea_story_wiki.py
  python import_arcaea_story_wiki.py --only "Eternal Core"
"""

from __future__ import annotations

import argparse
import re
import sys
import urllib.request
from pathlib import Path

try:
    from bs4 import BeautifulSoup, Tag
except ImportError as e:
    raise SystemExit("beautifulsoup4 が必要です: pip install beautifulsoup4") from e

from story_folder_names import load_pack_order, pack_folder_name

ROOT = Path(__file__).resolve().parents[1]
STORY_DIR = ROOT / "story"

WIKI_BASE = "https://wikiwiki.jp/arcaea/"
# (Markdown ファイル名（拡張子なし）, Wiki のページパス % エンコード済みパス)
PAGES: list[tuple[str, str]] = [
    ("Arcaea", "%E3%82%B9%E3%83%88%E3%83%BC%E3%83%AA%E3%83%BC/Main%20Story/Arcaea"),
    ("Eternal Core", "%E3%82%B9%E3%83%88%E3%83%BC%E3%83%AA%E3%83%BC/Main%20Story/Eternal%20Core"),
    ("Luminous Sky", "%E3%82%B9%E3%83%88%E3%83%BC%E3%83%AA%E3%83%BC/Main%20Story/Luminous%20Sky"),
    ("Vicious Labyrinth", "%E3%82%B9%E3%83%88%E3%83%BC%E3%83%AA%E3%83%BC/Main%20Story/Vicious%20Labyrinth"),
    ("Adverse Prelude", "%E3%82%B9%E3%83%88%E3%83%BC%E3%83%AA%E3%83%BC/Main%20Story/Adverse%20Prelude"),
    ("Black Fate", "%E3%82%B9%E3%83%88%E3%83%BC%E3%83%AA%E3%83%BC/Main%20Story/Black%20Fate"),
    ("Final Verdict", "%E3%82%B9%E3%83%88%E3%83%BC%E3%83%AA%E3%83%BC/Main%20Story/Final%20Verdict"),
]

SECTION_ID_RE = re.compile(
    r"^(?:\d+-\d+|\d+-[A-Za-z0-9]+|V-\d+|VS-\d+|F-\d+)$"
)


def _normalize_section_id(raw: str) -> str | None:
    """Wiki の strong 表記（英語併記など）を ## 見出し用の canonical id に。"""
    t = raw.strip("\u3000 \t\n")
    if t.startswith("終わりの末夢"):
        return "終わりの末夢"
    if t.startswith("無欠の願い"):
        return "無欠の願い"
    if SECTION_ID_RE.match(t):
        return t
    return None

USER_AGENT = "Mozilla/5.0 (compatible; nahi_pub-story-import/1.0; +local)"


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=180) as resp:
        return resp.read().decode("utf-8", "replace")


def _strong_section_id(tag: Tag) -> str | None:
    t = tag.get_text(strip=True)
    return _normalize_section_id(t)


def _first_japanese_fold_content(strong: Tag) -> Tag | None:
    for fc in strong.find_all_next("div", class_=lambda c: c and "fold-container" in c):
        for sm in fc.find_all(
            "div",
            class_=lambda c: c and "fold-summary" in c and "hidden-on-open" in c,
        ):
            if sm.get_text(strip=True) == "日本語":
                cont = fc.find(
                    "div",
                    class_=lambda c: c and "fold-content" in c and "visible-on-open" in c,
                )
                if cont:
                    return cont
    return None


def _ruby_to_simple_ruby(fragment: Tag) -> str:
    """rp 除去後、<br …> を改行へ。bs4 の br.replace_with は連続 br でツリーが壊れるため正規表現で処理。"""
    frag = BeautifulSoup(str(fragment), "html.parser")
    root = frag.find() or frag
    for rp in root.find_all("rp"):
        rp.decompose()
    inner = root.decode_contents() if hasattr(root, "decode_contents") else str(root)
    inner = re.sub(r"<br[^>]*>", "\n", inner, flags=re.I)
    inner = re.sub(r"</br\s*>", "", inner, flags=re.I)
    inner = re.sub(r"\n{3,}", "\n\n", inner)
    return inner.strip()


def _extract_sections(html: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    body = soup.select_one("#body")
    if not body:
        return []

    out: list[tuple[str, str]] = []
    for strong in body.find_all("strong"):
        sid = _strong_section_id(strong)
        if not sid:
            continue
        fc = _first_japanese_fold_content(strong)
        if not fc:
            continue
        # 長文は複数 <p> に分割されるため、折りたたみ内の段落をすべて結合する
        paras = fc.find_all("p")
        if paras:
            parts: list[str] = []
            for p in paras:
                h = _ruby_to_simple_ruby(p)
                if h:
                    parts.append(h)
            body_html = "\n\n".join(parts)
        else:
            body_html = _ruby_to_simple_ruby(fc)
        if not body_html:
            continue
        out.append((sid, body_html))
    return out


def _write_md(stem: str, wiki_url: str, sections: list[tuple[str, str]], out_path: Path) -> None:
    lines = [
        "---",
        f'title: "{stem}"',
        f"source: {wiki_url!r}",
        "---",
        "",
        f"# {stem}",
        "",
        "※ 本文は Arcaea Wiki から自動抽出。ルビは wiki の HTML を簡略化したもの。",
        "",
    ]
    for sid, body in sections:
        lines.append(f"## {sid}")
        lines.append("")
        lines.append(body)
        lines.append("")
    lines.append("## 解説（メモ）")
    lines.append("")
    lines.append("- 自動取り込み。表現の修正はゲーム内原文・ライセンスに従うこと。")
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Import Main Story pages from Arcaea Wiki into story/*.md")
    ap.add_argument("--only", default="", help="特定の話名のみ（例: Eternal Core）")
    ap.add_argument(
        "--skip",
        default="Arcaea",
        help="スキップする話名（カンマ区切り）。既定: Arcaea（手編集を上書きしない）",
    )
    ap.add_argument("--force-all", action="store_true", help="Arcaea も含めスキップしない")
    args = ap.parse_args()

    skip = set()
    if not args.force_all:
        skip = {s.strip() for s in args.skip.split(",") if s.strip()}

    only = args.only.strip()
    STORY_DIR.mkdir(parents=True, exist_ok=True)

    for stem, path in PAGES:
        if only and stem != only:
            continue
        if stem in skip:
            print(f"SKIP: {stem}")
            continue
        url = WIKI_BASE + path
        print(f"FETCH: {stem} …")
        try:
            html = _fetch(url)
        except Exception as e:
            print(f"FAIL: {stem}: {e}", file=sys.stderr)
            continue
        sections = _extract_sections(html)
        if not sections:
            print(f"WARN: {stem}: セクションが0件")
            continue
        order = load_pack_order(ROOT)
        file_stem = pack_folder_name(stem, order) if order else stem
        out_path = STORY_DIR / f"{file_stem}.md"
        _write_md(stem, url, sections, out_path)
        print(f"OK: {out_path} ({len(sections)} sections)")


if __name__ == "__main__":
    main()
