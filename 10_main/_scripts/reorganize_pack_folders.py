# -*- coding: utf-8 -*-
"""Deduplicate 10_main/pack folders and renumber by wiki 実装バージョン (ascending).

Reference: https://wikiwiki.jp/arcaea/%E3%83%91%E3%83%83%E3%82%AF%E9%A0%86

If removal of duplicate folders fails (e.g. OneDrive lock), run finish_pack_reorg.py
after this script has moved winners to __KEEP_* (it deletes leftovers and renames).
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parent.parent / "pack"

# (version_tuple, title) — order matches https://wikiwiki.jp/arcaea/%E3%83%91%E3%83%83%E3%82%AF%E9%A0%86
ORDERED: list[tuple[tuple[int, ...], str]] = [
    ((1, 0, 5), "Arcaea Pack"),
    ((1, 0, 5), "Eternal Core Pack"),
    ((1, 0, 11), "Crimson Solace Pack"),
    ((1, 1, 0), "Memory Archive"),
    ((1, 1, 2), "Dynamix Collaboration Pack"),
    ((1, 1, 4), "Ambivalent Vision Pack"),
    ((1, 5, 0), "Vicious Labyrinth Pack"),
    ((1, 5, 5), "Lanota Collaboration Pack"),
    ((1, 6, 0), "Binary Enfold Pack"),
    ((1, 7, 0), "Luminous Sky Pack"),
    ((1, 8, 0), "Tone Sphere Collaboration Pack"),
    ((1, 9, 0), "Groove Coaster Collaboration Pack"),
    ((2, 0, 0), "Absolute Reason Pack"),
    ((2, 1, 0), "CHUNITHM Collaboration Pack"),
    ((2, 2, 0), "Adverse Prelude Pack"),
    ((2, 3, 0), "Sunset Radiance Pack"),
    ((3, 0, 0), "Black Fate Pack"),
    ((3, 0, 0), "Extend Archive 1\uFF0D Visions Pack"),
    ((3, 3, 0), "Ephemeral Page Pack"),
    ((3, 4, 0), "O.N.G.E.K.I. Collaboration Pack"),
    ((3, 4, 1), "maimai Collaboration Pack"),
    ((3, 6, 0), "Esoteric Order Pack"),
    ((3, 8, 0), "WACCA Collaboration Pack"),
    ((3, 10, 0), "Divided Heart Pack"),
    ((3, 11, 0), "Muse Dash Collaboration Pack"),
    ((4, 0, 0), "Final Verdict Pack"),
    ((4, 2, 0), "Extend Archive 2\uFF0D Chronicles Pack"),
    ((4, 5, 0), "Cytus II Collaboration Pack"),
    ((4, 7, 0), "Lasting Eden Pack"),
    ((5, 5, 0), "Extend Archive 3\uFF0D Illusions Pack"),
    ((5, 9, 0), "Absolute Nihil Pack"),
    ((5, 10, 0), "Rotaeno Collaboration Pack"),
    ((6, 0, 0), "Lucent Historia Pack"),
    ((6, 3, 0), "UNDERTALE Collaboration Pack"),
    ((6, 5, 0), "DJMAX Collaboration Pack"),
    ((6, 7, 0), "Extant Anima Pack"),
    ((6, 9, 0), "Liminal Eclipse Pack"),
    ((6, 10, 0), "World Extend 4\uFF0D Emanations Pack"),
    ((6, 10, 8), "Arcaea Next Stage Pack"),
    ((6, 12, 0), "MEGAREX Collaboration Pack"),
    ((99, 0, 0), "Error Track (エイプリルフール楽曲)"),
]


def pack_key(name: str) -> str:
    m = re.match(r"^\d+_(.+)$", name)
    return m.group(1) if m else name


def md_count(d: Path) -> int:
    return sum(1 for _ in d.rglob("*.md"))


def main() -> None:
    if not PACK_ROOT.is_dir():
        raise SystemExit(f"Missing pack root: {PACK_ROOT}")

    dirs = [p for p in PACK_ROOT.iterdir() if p.is_dir()]
    by_key: dict[str, Path] = {}
    for p in dirs:
        key = pack_key(p.name)
        n = md_count(p)
        if key not in by_key or n > md_count(by_key[key]):
            by_key[key] = p

    # Phase 1: move winners to __KEEP_NN
    keep_dirs: list[Path] = []
    for i, (_, title) in enumerate(ORDERED, start=1):
        if title not in by_key:
            raise SystemExit(f"No folder found for pack: {title!r}")
        src = by_key[title]
        tmp = PACK_ROOT / f"__KEEP_{i:02d}"
        if tmp.exists():
            shutil.rmtree(tmp)
        src.rename(tmp)
        keep_dirs.append(tmp)

    # Phase 2: remove leftovers except _assets
    for p in list(PACK_ROOT.iterdir()):
        if not p.is_dir():
            continue
        if p.name.startswith("__KEEP_"):
            continue
        if p.name == "_assets":
            continue
        shutil.rmtree(p)

    # Phase 3: final names
    for i, (_, title) in enumerate(ORDERED, start=1):
        tmp = PACK_ROOT / f"__KEEP_{i:02d}"
        dest = PACK_ROOT / f"{i:02d}_{title}"
        if dest.exists():
            shutil.rmtree(dest)
        tmp.rename(dest)
        print(f"{i:02d} {title}")

    print("Done.")


if __name__ == "__main__":
    main()
