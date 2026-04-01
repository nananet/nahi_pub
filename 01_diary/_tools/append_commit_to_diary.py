#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
直近コミットを 01_diary/YYYY/YYYY-MM-DD.md の ## Timeline に追記する。
コミットの author date の日付ファイルを使う（「その日の日記」に寄せる）。

無限ループ防止: 直近コミット件名が diary: で始まる場合は何もしない。
環境変数 SKIP_DIARY_LOG=1 でもスキップ。

DIARY_AUTO_COMMIT=1 のとき、01_diary のみを diary: auto-log でコミットする（--no-verify）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _git(*args: str) -> str:
    return subprocess.check_output(["git", *args], text=True, stderr=subprocess.DEVNULL).strip()


def main() -> int:
    if os.environ.get("SKIP_DIARY_LOG", "").strip() in ("1", "true", "yes"):
        return 0

    try:
        subj = _git("log", "-1", "--pretty=%s")
    except subprocess.CalledProcessError:
        return 0

    if subj.lstrip().lower().startswith("diary:"):
        return 0

    try:
        author_iso = _git("log", "-1", "--pretty=%aI")
        sha = _git("log", "-1", "--pretty=%h")
    except subprocess.CalledProcessError:
        return 0

    try:
        dt = datetime.fromisoformat(author_iso.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.now(timezone.utc).astimezone()

    local = dt.astimezone()
    date_str = local.strftime("%Y-%m-%d")
    year = local.strftime("%Y")
    hm = local.strftime("%H:%M")

    root = Path(_git("rev-parse", "--show-toplevel"))
    diary_dir = root / "01_diary" / year
    diary_dir.mkdir(parents=True, exist_ok=True)
    diary_path = diary_dir / f"{date_str}.md"

    line = f"- {hm} `{sha}` {subj}\n"

    template = f"""---
tags:
  - diary
---

## TODO

## Timeline

{line}"""

    if diary_path.is_file():
        text = diary_path.read_text(encoding="utf-8")
        m = re.search(r"^## Timeline\s*\n", text, flags=re.M)
        if m:
            insert_at = m.end()
            text = text[:insert_at] + line + text[insert_at:]
        else:
            text = text.rstrip() + "\n\n## Timeline\n\n" + line
        diary_path.write_text(text, encoding="utf-8")
    else:
        diary_path.write_text(template, encoding="utf-8")

    if os.environ.get("DIARY_AUTO_COMMIT", "").strip() in ("1", "true", "yes"):
        try:
            subprocess.run(
                ["git", "add", "--", str(diary_path.relative_to(root))],
                cwd=root,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            st = subprocess.run(
                ["git", "diff", "--cached", "--quiet", "--", "01_diary/"],
                cwd=root,
            )
            if st.returncode == 0:
                return 0
            subprocess.run(
                [
                    "git",
                    "commit",
                    "-m",
                    "diary: auto-log",
                    "--no-verify",
                ],
                cwd=root,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, OSError):
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
