# -*- coding: utf-8 -*-
"""Finish reorg: remove leftover dirs, rename __KEEP_* -> NN_Title.

Use when reorganize_pack_folders.py failed mid-way (e.g. PermissionError on rmtree).
"""
from __future__ import annotations

import os
import shutil
import stat
import time
from pathlib import Path

PACK_ROOT = Path(__file__).resolve().parent.parent / "pack"

ORDERED_TITLES: list[str] = [
    "Arcaea Pack",
    "Eternal Core Pack",
    "Crimson Solace Pack",
    "Memory Archive",
    "Dynamix Collaboration Pack",
    "Ambivalent Vision Pack",
    "Vicious Labyrinth Pack",
    "Lanota Collaboration Pack",
    "Binary Enfold Pack",
    "Luminous Sky Pack",
    "Tone Sphere Collaboration Pack",
    "Groove Coaster Collaboration Pack",
    "Absolute Reason Pack",
    "CHUNITHM Collaboration Pack",
    "Adverse Prelude Pack",
    "Sunset Radiance Pack",
    "Black Fate Pack",
    "Extend Archive 1\uFF0D Visions Pack",
    "Ephemeral Page Pack",
    "O.N.G.E.K.I. Collaboration Pack",
    "maimai Collaboration Pack",
    "Esoteric Order Pack",
    "WACCA Collaboration Pack",
    "Divided Heart Pack",
    "Muse Dash Collaboration Pack",
    "Final Verdict Pack",
    "Extend Archive 2\uFF0D Chronicles Pack",
    "Cytus II Collaboration Pack",
    "Lasting Eden Pack",
    "Extend Archive 3\uFF0D Illusions Pack",
    "Absolute Nihil Pack",
    "Rotaeno Collaboration Pack",
    "Lucent Historia Pack",
    "UNDERTALE Collaboration Pack",
    "DJMAX Collaboration Pack",
    "Extant Anima Pack",
    "Liminal Eclipse Pack",
    "World Extend 4\uFF0D Emanations Pack",
    "Arcaea Next Stage Pack",
    "MEGAREX Collaboration Pack",
    "Error Track (エイプリルフール楽曲)",
]


def _on_rm_error(func, path, exc_info) -> None:
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def rmtree_robust(p: Path, retries: int = 5) -> None:
    for attempt in range(retries):
        try:
            shutil.rmtree(p, onerror=_on_rm_error)
            return
        except OSError:
            time.sleep(0.4 * (attempt + 1))
    shutil.rmtree(p, onerror=_on_rm_error)


def main() -> None:
    for p in list(PACK_ROOT.iterdir()):
        if not p.is_dir():
            continue
        if p.name.startswith("__KEEP_"):
            continue
        if p.name == "_assets":
            continue
        print("Removing", p.name)
        rmtree_robust(p)

    for i, title in enumerate(ORDERED_TITLES, start=1):
        tmp = PACK_ROOT / f"__KEEP_{i:02d}"
        dest = PACK_ROOT / f"{i:02d}_{title}"
        if not tmp.is_dir():
            raise SystemExit(f"Missing {tmp.name}")
        if dest.exists():
            rmtree_robust(dest)
        tmp.rename(dest)
        print(f"{i:02d} {title}")

    print("Done.")


if __name__ == "__main__":
    main()
