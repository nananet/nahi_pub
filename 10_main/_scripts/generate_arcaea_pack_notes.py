# -*- coding: utf-8 -*-
"""
Arcaea Wiki「パック順」ページのテキストから Obsidian 用の楽曲ノートを生成する。
入力: 10_main/_data/arcaea_pack_page.txt
任意: 10_main/_data/arcaea_chart_constants.json（楽曲 Wiki URL をキーに PST/PRS/FTR/BYD/ETR の譜面定数）
出力: 10_main/pack/<NN>_<パック名>/<曲>.md
      10_main/譜面定数/<8, 8+, 9, 9+, …>.md（帯ごと・帯内は 0.1 見出し）および _index.md
  NN … 各パック見出しブロック内の「実装 : ver.x.x.x」の最小バージョンが古い順

オプション:
  --clean             pack 出力を削除してから再生成
  --fetch-jackets     各楽曲 Wiki からジャケット URL・別名(h2)を取得
  --refresh-jackets   キャッシュを無視して再取得
  --download-jackets  キャッシュから画像を _assets/jackets に保存
  --fetch-chart-constants   各楽曲 Wiki から攻略情報の「◯◯ 譜面定数」を読み取り JSON に追記
  --refresh-chart-constants 上記でキャッシュを無視して全 URL を再取得
  --fetch-wiki-diff-tiers   楽曲 Wiki の表「Difficulty」行から PST/PRS/FTR/ETR/BYD の列順を取得（ETR と BYD を区別）
  --refresh-wiki-diff-tiers 上記キャッシュを全 URL 再取得
"""
from __future__ import annotations

import functools
import json
import posixpath
import random
import re
import shutil
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, unquote, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "_data" / "arcaea_pack_page.txt"
JACKET_CACHE = ROOT / "_data" / "arcaea_jacket_cache.json"
SONG_META_CACHE = ROOT / "_data" / "arcaea_song_meta_cache.json"
CHART_CONSTANTS_CACHE = ROOT / "_data" / "arcaea_chart_constants.json"
WIKI_DIFF_TIERS_CACHE = ROOT / "_data" / "arcaea_wiki_diff_tiers.json"
OUT_PACK = ROOT / "pack"
JACKET_DIR = OUT_PACK / "_assets" / "jackets"
PACK_BASE = "10_main/pack"
JACKET_ASSETS_VAULT = f"{PACK_BASE}/_assets/jackets"
# パック表の難易列の標準順（Beyond → Eternal）。Wiki の表ヘッダが途中までしか取れないときの続きにも使う
STD_DIFF_TIER_ORDER = ("PST", "PRS", "FTR", "BYD", "ETR")
CONST_HUB_DIR = ROOT / "譜面定数"
CONST_HUB_VAULT = "10_main/譜面定数"
CONST_HUB_MIN = 8.0
CONST_HUB_MAX = 12.0
# 譜面定数ページ表内ジャケット（正方形・object-fit で統一）
CONST_HUB_JACKET_THUMB = 72

VER_RE = re.compile(r"^(\d+\.\d+\.\d+|\d+\.\d+)\(")
PACK_VER_RE = re.compile(r"実装\s*:\s*ver\.(\d+)\.(\d+)\.(\d+)", re.I)

# Arcaea Wiki 楽曲ページの wikicolor（表ヘッダ）に合わせた色（HTML の color 名を hex に正規化）
DIFF_STYLE = {
    "PST": "#00FFFF",  # aqua
    "PRS": "#00FF00",  # lime
    "FTR": "#FF00FF",  # fuchsia
    "BYD": "#FF0000",  # red
    "ETR": "#9370DB",  # mediumpurple（Wiki の Eternal 表記）
}

# Wiki パック順表は PST|PRS|FTR|ETR|BYD 見出しでも、行によっては難易セルが4つまでしかなく、
# 4列目はゲーム上の Beyond(BYD) に相当することが多い（Eternal 導入前の列の扱いの名残）。
# h2 別名がなくてもゲーム上「別曲」扱いの BYD 譜面はここに URL → 別ノート用タイトルを足す。
FORCE_BYD_ALT_TITLES: dict[str, str] = {
    "https://wikiwiki.jp/arcaea/Axium%20Crisis": "Axium Divergence",
}

# Wiki 取得前でも、既知の BYD 別ジャケット（::ALT）をキャッシュに補う
DEFAULT_ALT_JACKET_OVERRIDES: dict[str, str] = {
    "https://wikiwiki.jp/arcaea/Axium%20Crisis::ALT": (
        "https://cdn.wikiwiki.jp/to/w/arcaea/Axium%20Crisis/::ref/"
        "Axium%20Crisis-b.jpg.webp?rev=2f9292538ba4f7f0ba4e45ebbab9e45d&t=20260309213616"
    ),
}

# 攻略情報内の「Past 譜面定数：3.0」「Future 譜面定数 : 7.8」など（英語難易名）
WIKI_CHART_CONST_NAME_TO_LAB: dict[str, str] = {
    "past": "PST",
    "present": "PRS",
    "future": "FTR",
    "beyond": "BYD",
    "eternal": "ETR",
}
WIKI_CHART_CONST_RE = re.compile(
    r"(Past|Present|Future|Beyond|Eternal)\s*譜面定数\s*[:：]\s*(\d+(?:\.\d+)?)",
    re.I,
)

JACKET_IMG_RE = re.compile(
    r'https://cdn\.wikiwiki\.jp/to/w/arcaea/[^"\s<>]+/::ref/[^"\s<>]+\.(?:jpg|jpeg|png|webp)(?:\?[^"\s<>]*)?',
    re.I,
)
JACKET_SKIP_SUBSTR = ("Lock_white", "header", "FrontPage", "sound-collection", "switch.jpg")


def parse_md_link(cell: str) -> tuple[str, str] | None:
    if not cell.startswith("["):
        return None
    i = 1
    depth = 1
    while i < len(cell) and depth:
        if cell[i] == "[":
            depth += 1
        elif cell[i] == "]":
            depth -= 1
        i += 1
    if depth != 0 or i > len(cell) or cell[i - 1] != "]":
        return None
    if i >= len(cell) or cell[i] != "(":
        return None
    title = cell[1 : i - 1]
    j = cell.find(")", i + 1)
    if j == -1:
        return None
    url = cell[i + 1 : j]
    return title, url


def pack_dir_from_h2(h2: str) -> str:
    s = re.sub(r"\s*\[[^\]]*曲[^\]]*\]\s*$", "", h2)
    s = s.replace('"', "").strip()
    for c in '<>:"/\\|?*':
        s = s.replace(c, "－")
    return s.strip() or "Unknown"


def pack_display_name(prefixed_dir: str) -> str:
    m = re.match(r"^\d{2,3}_(.+)$", prefixed_dir)
    return m.group(1) if m else prefixed_dir


def min_pack_version_from_body(body: str) -> tuple[int, int, int]:
    found = PACK_VER_RE.findall(body)
    if not found:
        return (999, 99, 99)
    tuples = [tuple(int(x) for x in t) for t in found]
    return min(tuples)


def split_pack_sections(text: str) -> list[tuple[str, str, int]]:
    """(pack_name, section_body, h2_line_index) のリスト（ファイル出現順）"""
    lines = text.splitlines()
    sections: list[tuple[str, str, int]] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^##\s+(.+)$", lines[i])
        if m:
            start_line = i
            name = pack_dir_from_h2(m.group(1).strip())
            i += 1
            body_chunks: list[str] = []
            while i < len(lines) and not re.match(r"^##\s+", lines[i]):
                body_chunks.append(lines[i])
                i += 1
            sections.append((name, "\n".join(body_chunks), start_line))
            continue
        i += 1
    return sections


def parse_song_version_cell(ver: str) -> tuple[int, int, int]:
    ver = ver.strip()
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", ver)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"^(\d+)\.(\d+)", ver)
    if m:
        return (int(m.group(1)), int(m.group(2)), 0)
    return (999, 99, 99)


def normalize_md_filename(filename: str) -> str:
    """Wiki ページ名末尾が「.」のとき Title + .md が Title..md になるのを Title.md に直す。"""
    if len(filename) < 4 or not filename.lower().endswith(".md"):
        return filename
    return re.sub(r"(.*?)\.+md$", r"\1.md", filename, flags=re.IGNORECASE)


def filename_from_url(url: str) -> str:
    path = urlparse(url).path.rstrip("/")
    last = path.split("/")[-1] if path else "song"
    name = unquote(last)
    for c in '\\/:*?"<>|':
        name = name.replace(c, "－")
    if not name.endswith(".md"):
        name = name + ".md"
    return normalize_md_filename(name)


def filename_from_title(title: str) -> str:
    name = title
    for c in '\\/:*?"<>|':
        name = name.replace(c, "－")
    return normalize_md_filename(name + ".md")


def ext_from_cdn_url(url: str) -> str:
    path = urlparse(url).path.lower()
    if path.endswith(".webp"):
        return ".webp"
    if path.endswith(".png"):
        return ".png"
    if path.endswith(".jpg") or path.endswith(".jpeg"):
        return ".jpg"
    return ".webp"


def jacket_vault_rel_if_exists(stem: str, ext: str) -> str | None:
    """stem+ext を優先。旧 stem.+ext のみあるときは stem+ext にリネーム。"""
    primary = JACKET_DIR / f"{stem}{ext}"
    if primary.is_file():
        return f"{JACKET_ASSETS_VAULT}/{primary.name}"
    legacy = JACKET_DIR / f"{stem}.{ext}"
    if legacy.is_file():
        try:
            legacy.rename(primary)
        except OSError:
            return f"{JACKET_ASSETS_VAULT}/{legacy.name}"
        return f"{JACKET_ASSETS_VAULT}/{primary.name}"
    return None


def fetch_wiki_html(url: str) -> str:
    req = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; nahi_pub pack note generator)"},
    )
    with urlopen(req, timeout=45) as r:
        return r.read().decode("utf-8", "replace")


def fetch_wiki_html_with_retry(url: str, max_retries: int = 5) -> str:
    for attempt in range(max_retries):
        try:
            return fetch_wiki_html(url)
        except HTTPError as e:
            if e.code in (429, 502, 503, 504) and attempt < max_retries - 1:
                time.sleep(2**attempt + random.uniform(0.2, 0.8))
                continue
            raise
        except URLError:
            if attempt < max_retries - 1:
                time.sleep(1.5 + random.uniform(0, 0.5))
                continue
            raise
    raise RuntimeError("fetch_wiki_html_with_retry: unreachable")


def strip_html_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s)


def parse_song_h2_titles(html: str) -> tuple[str | None, str | None]:
    blocks: list[str] = []
    for pat in (
        r'<h2[^>]*id="h2_content_1_0"[^>]*>(.*?)</h2>',
        r"<h2[^>]*>(.*?)</h2>",
    ):
        for m in re.finditer(pat, html, re.DOTALL | re.I):
            inner = strip_html_tags(m.group(1))
            inner = re.sub(r"\s+", " ", inner).strip()
            if inner.upper() in ("CONTENTS", "MENU", ""):
                continue
            blocks.append(inner)
        if blocks:
            break
    if not blocks:
        return None, None
    inner = blocks[0]
    m2 = re.match(r"^(.+?)\s*\((.+)\)\s*$", inner)
    if not m2:
        return inner, None
    a, b = m2.group(1).strip(), m2.group(2).strip()
    if a == b:
        return a, None
    return a, b


def collect_jacket_urls(html: str) -> list[str]:
    html = html.replace("&amp;", "&")
    out: list[str] = []
    for m in JACKET_IMG_RE.finditer(html):
        u = m.group(0)
        if any(s in u for s in JACKET_SKIP_SUBSTR):
            continue
        out.append(u)
    return out


def pick_jacket_url(urls: list[str], *, byd_variant: bool) -> str | None:
    song_refs = [u for u in urls if "/::ref/" in u and "FrontPage" not in u]
    if not song_refs:
        return None
    if byd_variant:
        for u in song_refs:
            if re.search(r"[-_]b\.(jpg|webp|png)", u, re.I):
                return u
        if len(song_refs) > 1:
            return song_refs[-1]
        return song_refs[0]
    for u in song_refs:
        if not re.search(r"[-_]b\.(jpg|webp|png)", u, re.I):
            return u
    return song_refs[0]


def jacket_url_from_wiki_html(html: str) -> str | None:
    return pick_jacket_url(collect_jacket_urls(html), byd_variant=False)


def load_jacket_cache() -> dict[str, str]:
    if not JACKET_CACHE.is_file():
        return {}
    try:
        data = json.loads(JACKET_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out = {str(k): str(v) for k, v in data.items() if v}
    for k, v in list(out.items()):
        if k.endswith("::BYD"):
            nk = k.replace("::BYD", "::ALT")
            if nk not in out:
                out[nk] = v
    return out


def save_jacket_cache(cache: dict[str, str]) -> None:
    JACKET_CACHE.parent.mkdir(parents=True, exist_ok=True)
    JACKET_CACHE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_chart_constants() -> dict[str, dict[str, str]]:
    if not CHART_CONSTANTS_CACHE.is_file():
        return {}
    try:
        raw = json.loads(CHART_CONSTANTS_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[str, dict[str, str]] = {}
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        inner = {
            str(lk): str(lv).strip()
            for lk, lv in v.items()
            if lv is not None and str(lv).strip()
        }
        if inner:
            out[str(k)] = inner
    return out


def chart_constant_cell(constants: dict[str, dict[str, str]], url: str, lab: str) -> str:
    """難易表の譜面定数セル。8.0 以上は譜面定数帯ページへリンク。BYD/ETR は行ごとにその難易のみ。"""
    row = constants.get(url) or {}
    v = (row.get(lab) or "").strip()
    if not v:
        return "—"
    esc = md_escape(v)
    fv = parse_chart_const_float(v)
    if fv is None or fv < 8.0 - 1e-9:
        return esc
    fk = f"{round(fv, 1):.1f}"
    stem = _const_hub_fine_to_merged_stem_map().get(fk)
    if not stem:
        return esc
    # 表の見た目は数値だけにしたいので、Markdown リンクで表示テキストを制御する
    rel = rel_pack_song_md_to_const_hub_merged_mdlink(stem, heading=fk)
    inner = _hub_angle_bracket_path(rel)
    return f"[{esc}](<{inner}>)"


def parse_chart_constants_from_html(html: str) -> dict[str, str]:
    """Wiki 楽曲ページ本文から攻略の譜面定数表記を抽出（PST/PRS/FTR/BYD/ETR）。"""
    text = strip_html_tags(html)
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text)
    out: dict[str, str] = {}
    for m in WIKI_CHART_CONST_RE.finditer(text):
        lab = WIKI_CHART_CONST_NAME_TO_LAB.get(m.group(1).lower())
        if lab:
            out[lab] = m.group(2)
    return out


def merge_chart_constants_from_html(
    chart_constants: dict[str, dict[str, str]], url: str, html: str
) -> None:
    parsed = parse_chart_constants_from_html(html)
    if not parsed:
        return
    row = chart_constants.setdefault(url, {})
    row.update(parsed)


def save_chart_constants(cache: dict[str, dict[str, str]]) -> None:
    CHART_CONSTANTS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    CHART_CONSTANTS_CACHE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_song_meta() -> dict[str, dict]:
    paths = [
        SONG_META_CACHE,
        ROOT / "_data" / "arcaea_byd_meta_cache.json",
    ]
    for path in paths:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        meta = {str(k): v for k, v in data.items() if isinstance(v, dict)}
        for v in meta.values():
            if "jacket_alt" not in v and v.get("jacket_byd"):
                v["jacket_alt"] = v["jacket_byd"]
        return meta
    return {}


def save_song_meta(meta: dict[str, dict]) -> None:
    SONG_META_CACHE.parent.mkdir(parents=True, exist_ok=True)
    SONG_META_CACHE.write_text(
        json.dumps(meta, ensure_ascii=False, indent=0, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def cache_key_main(url: str) -> str:
    return url


def cache_key_alt(url: str) -> str:
    return url + "::ALT"


def is_diff_cell(s: str) -> bool:
    s = s.strip()
    if not s:
        return False
    s = re.sub(r"\*+", "", s)
    if re.fullmatch(r"\d{1,2}\+\+?", s):
        return True
    if re.fullmatch(r"\d{1,2}", s):
        return True
    if re.fullmatch(r"1[012]\+?", s):
        return True
    if s in ("?",):
        return True
    if re.fullmatch(r"[\d\?\+\*x×]+", s, re.I):
        return True
    if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", s):
        return False
    return bool(re.search(r"\d", s))


def split_diffs_and_genre(parts_after_ver: list[str]) -> list[str]:
    cells = [p.strip() for p in parts_after_ver]
    while cells and not is_diff_cell(cells[-1]):
        cells.pop()
    return cells


def map_difficulty_cells(diffs_raw: list[str]) -> dict[str, str]:
    """Wiki 楽曲ページの列順キャッシュが無いときのフォールバック（4 セル行は多くが BYD 終端）。"""
    n = len(diffs_raw)
    if n == 0:
        return {}
    if n >= 5:
        return {STD_DIFF_TIER_ORDER[i]: diffs_raw[i] for i in range(5)}
    if n == 4:
        return {
            "PST": diffs_raw[0],
            "PRS": diffs_raw[1],
            "FTR": diffs_raw[2],
            "BYD": diffs_raw[3],
        }
    return {STD_DIFF_TIER_ORDER[i]: diffs_raw[i] for i in range(n)}


WIKI_TIER_HEADER_MAP = {
    "past": "PST",
    "present": "PRS",
    "future": "FTR",
    "beyond": "BYD",
    "eternal": "ETR",
}


def parse_wiki_song_difficulty_tier_order(html: str) -> list[str] | None:
    """楽曲 Wiki HTML の情報表「Difficulty」行から、難易列の順（PST…）を得る。"""
    m = re.search(
        r"Difficulty\s*</t[hd]>((?:\s*<t[hd][^>]*>.*?</t[hd]>\s*)+?)\s*</tr>",
        html,
        re.I | re.DOTALL,
    )
    if not m:
        return None
    chunk = m.group(1)
    tiers: list[str] = []
    for m2 in re.finditer(r"<t[hd][^>]*>(.*?)</t[hd]>", chunk, re.I | re.DOTALL):
        inner = re.sub(r"\s+", " ", strip_html_tags(m2.group(1))).strip()
        low = inner.lower()
        if low in WIKI_TIER_HEADER_MAP:
            tiers.append(WIKI_TIER_HEADER_MAP[low])
    return tiers or None


def load_wiki_diff_tiers() -> dict[str, list[str]]:
    if not WIKI_DIFF_TIERS_CACHE.is_file():
        return {}
    try:
        raw = json.loads(WIKI_DIFF_TIERS_CACHE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out: dict[str, list[str]] = {}
    for k, v in raw.items():
        if isinstance(v, list) and all(isinstance(x, str) for x in v):
            out[str(k)] = list(v)
    return out


def save_wiki_diff_tiers(cache: dict[str, list[str]]) -> None:
    WIKI_DIFF_TIERS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    WIKI_DIFF_TIERS_CACHE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=0, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def merge_wiki_diff_tiers_from_html(
    tier_cache: dict[str, list[str]], url: str, html: str
) -> None:
    tiers = parse_wiki_song_difficulty_tier_order(html)
    if tiers:
        tier_cache[url] = tiers


def _chart_row_has(const_row: dict[str, str], lab: str) -> bool:
    return bool((const_row.get(lab) or "").strip())


def _relabel_ettr_byd_single_high(
    diffs: dict[str, str], const_row: dict[str, str]
) -> dict[str, str]:
    """1 つの高難易セルだけが ETR に付いているが定数は Beyond のみ、→ BYD に付け替え。"""
    if "BYD" in diffs or "ETR" not in diffs:
        return diffs
    has_b = _chart_row_has(const_row, "BYD")
    has_e = _chart_row_has(const_row, "ETR")
    if has_b and not has_e:
        out = dict(diffs)
        out["BYD"] = out.pop("ETR")
        return out
    if not has_b and not has_e:
        out = dict(diffs)
        out["BYD"] = out.pop("ETR")
        return out
    return diffs


def difficulties_from_raw_and_tier_order(
    diffs_raw: list[str],
    tier_order: list[str] | None,
    const_row: dict[str, str] | None = None,
) -> dict[str, str]:
    n = len(diffs_raw)
    if n == 0:
        return {}
    cr = const_row or {}
    to = list(tier_order) if tier_order else []

    if to and len(to) == n:
        out = {to[i]: diffs_raw[i] for i in range(n)}
        return _relabel_ettr_byd_single_high(out, cr)

    std = STD_DIFF_TIER_ORDER
    if to and len(to) < n <= 5 and list(to) == list(std[: len(to)]):
        rest = list(std[len(to) : len(to) + (n - len(to))])
        if len(rest) == n - len(to):
            out = {to[i]: diffs_raw[i] for i in range(len(to))}
            for j, lab in enumerate(rest):
                out[lab] = diffs_raw[len(to) + j]
            return _relabel_ettr_byd_single_high(out, cr)

    return map_difficulty_cells(diffs_raw)


def reorder_rec_difficulties_from_tiers(
    rec: dict,
    tier_cache: dict[str, list[str]],
    chart_constants: dict[str, dict[str, str]] | None = None,
) -> None:
    raw = rec.get("_diffs_raw")
    if not raw:
        return
    url = rec["url"]
    const_row = (chart_constants or {}).get(url) or {}
    rec["difficulties"] = difficulties_from_raw_and_tier_order(
        raw, tier_cache.get(url), const_row
    )


def apply_wiki_diff_tiers_to_pack(
    by_pack: dict[str, list[dict]],
    pack_order: list[str],
    tier_cache: dict[str, list[str]],
    chart_constants: dict[str, dict[str, str]] | None = None,
) -> None:
    for pname in pack_order:
        for rec in by_pack.get(pname, []):
            reorder_rec_difficulties_from_tiers(rec, tier_cache, chart_constants)


def parse_song_row(line: str) -> dict | None:
    line = line.rstrip()
    if not line.startswith("|"):
        return None
    raw = [p.strip() for p in line.split("|")]
    while raw and raw[0] == "":
        raw.pop(0)
    while raw and raw[-1] == "":
        raw.pop()
    if len(raw) < 4:
        return None
    parsed = parse_md_link(raw[0])
    if not parsed:
        return None
    title, url = parsed
    if "wikiwiki.jp/arcaea" not in url:
        return None
    if raw[1] in ("Composer", "---", "PST", "PRS", "Song"):
        return None
    if raw[0].startswith("[Song]"):
        return None
    composer = raw[1]
    version = raw[2]
    if not VER_RE.match(version) and not re.match(r"^\d+\.\d+", version):
        return None
    diffs_raw = split_diffs_and_genre(raw[3:])
    return {
        "title": title,
        "url": url,
        "composer": composer,
        "version": version,
        "_diffs_raw": list(diffs_raw),
        "difficulties": map_difficulty_cells(diffs_raw),
    }


def md_escape(s: str) -> str:
    return s.replace("|", "\\|")


def diff_label_html(lab: str) -> str:
    col = DIFF_STYLE.get(lab, "#333")
    return f'<span style="color:{col}">{lab}</span>'


def jacket_wikilink_from_pack_note(jacket_vault: str) -> str:
    """pack/<パック名>/<曲>.md から見た _assets 相対パス（10_main をルートにしても解決しやすい）。"""
    v = (jacket_vault or "").strip()
    if not v:
        return ""
    base = Path(v.replace("\\", "/")).name
    if not base:
        return ""
    return posixpath.normpath(f"../_assets/jackets/{base}").replace("\\", "/")


def song_markdown(
    rec: dict, pack_dir_name: str, chart_constants: dict[str, dict[str, str]]
) -> str:
    display = pack_display_name(pack_dir_name)
    jacket_vault = (rec.get("jacket_vault_path") or "").strip()
    jacket_wl = jacket_wikilink_from_pack_note(jacket_vault)
    fm = [
        "---",
        f'title: "{rec["title"].replace(chr(34), chr(39))}"',
        f'pack: "{display.replace(chr(34), chr(39))}"',
        f'composer: "{rec["composer"].replace(chr(34), chr(39))}"',
        f'初出バージョン: "{rec["version"]}"',
        "source: arcaea-wiki-pack-order",
    ]
    if rec.get("is_alt_chart_variant"):
        fm.append("alt_chart_variant: true")
    if jacket_wl:
        fm.append(f'jacket: "{jacket_wl.replace(chr(34), chr(39))}"')
    fm.extend(["---", ""])
    lines = [
        *fm,
        f"# {rec['title']}",
        "",
    ]
    if jacket_wl:
        lines.extend([f"![[{jacket_wl}]]", ""])
    lines.extend(
        [
            f"- **パック**: {display}",
            f"- **コンポーザー**: {rec['composer']}",
            f"- **初出バージョン（Wiki表記）**: {rec['version']}",
            f"- **参照**: [{rec['title']}]({rec['url']})",
            "",
            "## 難易度",
            "",
            "| 譜面 | レベル | 譜面定数 |",
            "| --- | --- | --- |",
        ]
    )
    url = rec["url"]
    diffs = rec.get("difficulties") or {}
    for lab in STD_DIFF_TIER_ORDER:
        raw_lv = (diffs.get(lab) or "").strip()
        if not raw_lv or raw_lv in ("?", "-", "—"):
            v = "—"
        else:
            v = md_escape(raw_lv)
        c = chart_constant_cell(chart_constants, url, lab)
        lines.append(f"| {diff_label_html(lab)} | {v} | {c} |")
    lines.append("")
    return "\n".join(lines)


def parse_section_songs(body: str) -> list[dict]:
    """パック内の Wiki 表における曲の並び（0,1,2,…）を _wiki_row に格納する。"""
    songs: list[dict] = []
    for line in body.splitlines():
        rec = parse_song_row(line)
        if rec:
            rec["_wiki_row"] = len(songs)
            songs.append(rec)
    return songs


def level_cell_is_real(s: str) -> bool:
    t = s.strip()
    if not t or t in ("?", "-", "—"):
        return False
    return True


def expand_subtitle_variants(
    songs: list[dict],
    *,
    do_fetch: bool,
    refresh: bool,
    jacket_cache: dict[str, str],
    song_meta: dict[str, dict],
    chart_constants: dict[str, dict[str, str]] | None = None,
    wiki_diff_tiers: dict[str, list[str]] | None = None,
) -> list[dict]:
    """Wiki h2 の別名、または FORCE_BYD_ALT_TITLES により、高難易を別ノートに分離する。"""
    out: list[dict] = []
    for rec in songs:
        url = rec["url"]

        meta: dict = {}
        need_fetch = do_fetch and (refresh or url not in song_meta or not song_meta.get(url))
        if need_fetch:
            try:
                html = fetch_wiki_html_with_retry(url)
                if chart_constants is not None:
                    merge_chart_constants_from_html(chart_constants, url, html)
                if wiki_diff_tiers is not None:
                    merge_wiki_diff_tiers_from_html(wiki_diff_tiers, url, html)
                    reorder_rec_difficulties_from_tiers(
                        rec, wiki_diff_tiers, chart_constants
                    )
                main_h2, sub_h2 = parse_song_h2_titles(html)
                urls = collect_jacket_urls(html)
                j_main = pick_jacket_url(urls, byd_variant=False)
                j_alt = pick_jacket_url(urls, byd_variant=True)
                meta = {
                    "main_title": main_h2 or rec["title"],
                    "subtitle": (sub_h2 or "").strip(),
                    "jacket_main": j_main or "",
                    "jacket_alt": j_alt or "",
                }
                song_meta[url] = meta
                if j_main:
                    jacket_cache[cache_key_main(url)] = j_main
                if j_alt and j_alt != j_main:
                    jacket_cache[cache_key_alt(url)] = j_alt
                time.sleep(0.45)
            except (HTTPError, URLError, OSError, TimeoutError):
                meta = song_meta.get(url, {})
        else:
            meta = song_meta.get(url, {})

        diffs = dict(rec.get("difficulties") or {})
        etr = diffs.get("ETR", "").strip()
        byd = diffs.get("BYD", "").strip()
        has_hi = level_cell_is_real(etr) or level_cell_is_real(byd)
        if not has_hi:
            rec["_sort_key"] = (
                parse_song_version_cell(rec["version"]),
                rec.get("_wiki_row", 0),
                0,
            )
            out.append(rec)
            continue

        subtitle = (meta.get("subtitle") or "").strip()
        force_alt = (FORCE_BYD_ALT_TITLES.get(url) or "").strip()
        # Wiki h2 が「GOODTEK (Arcaea Edit)」のように括弧内副題に分かれていても、
        # パック表の曲名に副題が含まれるだけなら別譜面ではない（例: GOODTEK + 副題 Arcaea Edit）。
        subtitle_means_separate_chart = bool(subtitle) and subtitle != rec["title"].strip()
        if subtitle_means_separate_chart and len(subtitle) >= 5 and subtitle in rec["title"]:
            subtitle_means_separate_chart = False
        split_ok = has_hi and (subtitle_means_separate_chart or bool(force_alt))

        if split_ok:
            main = dict(rec)
            main.pop("_diffs_raw", None)
            md = dict(main["difficulties"])
            hi: dict[str, str] = {}
            if "ETR" in md and level_cell_is_real(md["ETR"]):
                hi["ETR"] = md["ETR"]
                del md["ETR"]
            if "BYD" in md and level_cell_is_real(md["BYD"]):
                hi["BYD"] = md["BYD"]
                del md["BYD"]
            main["difficulties"] = md
            main["jacket_cdn"] = meta.get("jacket_main") or jacket_cache.get(cache_key_main(url), "")
            main["_sort_key"] = (
                parse_song_version_cell(main["version"]),
                rec.get("_wiki_row", 0),
                0,
            )
            out.append(main)

            alt_title = force_alt or subtitle
            alt_jacket = (
                meta.get("jacket_alt")
                or jacket_cache.get(cache_key_alt(url), "")
                or meta.get("jacket_main")
                or jacket_cache.get(cache_key_main(url), "")
                or jacket_cache.get(url, "")
            )
            alt_rec = {
                "title": alt_title,
                "url": url,
                "composer": rec["composer"],
                "version": rec["version"],
                "difficulties": hi,
                "jacket_cdn": alt_jacket,
                "is_alt_chart_variant": True,
                "_sort_key": (
                    parse_song_version_cell(rec["version"]),
                    rec.get("_wiki_row", 0),
                    1,
                ),
            }
            out.append(alt_rec)
        else:
            rec["jacket_cdn"] = meta.get("jacket_main") or jacket_cache.get(cache_key_main(url), "")
            rec["_sort_key"] = (
                parse_song_version_cell(rec["version"]),
                rec.get("_wiki_row", 0),
                0,
            )
            out.append(rec)

    out.sort(key=lambda r: r.get("_sort_key", ((999, 99, 99), 0, 0)))
    for r in out:
        r.pop("_sort_key", None)
        r.pop("_wiki_row", None)
        r.pop("_diffs_raw", None)
    return out


def download_binary(url: str, dest: Path) -> None:
    req = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (compatible; nahi_pub pack note generator)"},
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(req, timeout=90) as r:
        dest.write_bytes(r.read())


def download_jacket_with_retry(cdn_url: str, dest: Path, max_retries: int = 4) -> bool:
    for attempt in range(max_retries):
        try:
            download_binary(cdn_url, dest)
            return True
        except (HTTPError, URLError, OSError, TimeoutError):
            if attempt < max_retries - 1:
                time.sleep(1.0 + random.uniform(0, 0.4))
                continue
            return False


def parse_chart_const_float(s: str) -> float | None:
    try:
        return float(str(s).strip())
    except ValueError:
        return None


def const_hub_fine_bucket_strings() -> list[str]:
    """0.1 刻みの定数キー（集計・並びの内部用）。"""
    n = int(round((CONST_HUB_MAX - CONST_HUB_MIN) / 0.1)) + 1
    return [f"{round(CONST_HUB_MIN + i * 0.1, 1):.1f}" for i in range(n)]


def const_hub_fine_span(lo: float, hi: float) -> list[str]:
    """[lo, hi] を 0.1 刻みの文字列キーに展開（浮動小数誤差を避けるため round）。"""
    out: list[str] = []
    x = round(lo, 1)
    hi_r = round(hi, 1)
    while x <= hi_r + 1e-6:
        out.append(f"{x:.1f}")
        x = round(x + 0.1, 1)
    return out


def const_hub_merged_pages() -> list[tuple[str, str, list[str]]]:
    """(ファイル stem, ページ見出しラベル, 含む 0.1 定数キー)。11.8 は 11+（11.8〜11.9）に含める。"""
    return [
        ("8", "8", const_hub_fine_span(8.0, 8.6)),
        ("8+", "8+", const_hub_fine_span(8.7, 8.9)),
        ("9", "9", const_hub_fine_span(9.0, 9.6)),
        ("9+", "9+", const_hub_fine_span(9.7, 9.9)),
        ("10", "10", const_hub_fine_span(10.0, 10.6)),
        ("10+", "10+", const_hub_fine_span(10.7, 10.9)),
        ("11", "11", const_hub_fine_span(11.0, 11.7)),
        ("11+", "11+", const_hub_fine_span(11.8, 11.9)),
        ("12", "12", const_hub_fine_span(12.0, 12.0)),
    ]


@functools.lru_cache(maxsize=1)
def _const_hub_fine_to_merged_stem_map() -> dict[str, str]:
    """0.1 定数キー → 帯ページのファイル stem（8 / 8+ / …）。"""
    d: dict[str, str] = {}
    for stem, _label, fine_list in const_hub_merged_pages():
        for fk in fine_list:
            d[fk] = stem
    return d


def rel_pack_song_md_to_const_hub_merged_mdlink(merged_stem: str, *, heading: str) -> str:
    """pack/<パック>/<曲>.md から見た譜面定数帯ノートへの相対パス（見出しジャンプ付き）。"""
    hub = CONST_HUB_DIR.name.replace("\\", "/")
    rel = posixpath.normpath(f"../../{hub}/{merged_stem}.md").replace("\\", "/")
    return f"{rel}#{heading}"


def _hub_html_esc(s: str) -> str:
    return (
        (s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _hub_encode_rel_href(rel: str) -> str:
    rel = rel.replace("\\", "/")
    parts: list[str] = []
    for p in rel.split("/"):
        if p in ("", ".", ".."):
            parts.append(p)
        else:
            parts.append(quote(p, safe=""))
    return "/".join(parts)


def _hub_relpath_from_hub_dir(target_vault_path: str) -> str:
    return posixpath.relpath(
        target_vault_path.replace("\\", "/"),
        CONST_HUB_VAULT,
    )


def _hub_angle_bracket_path(rel: str) -> str:
    """Markdown の [](<...>) / ![](<...>) 用。終端の > とパス内の < > だけ調整。"""
    rel = rel.replace("\\", "/")
    return rel.replace("<", "%3C").replace(">", "%3E")


def _hub_jacket_cell_html(jacket_vault: str, thumb: int) -> str:
    """譜面定数表のサムネ。固定枠 + object-fit:cover で元画像の縦横比差を吸収する。"""
    j = (jacket_vault or "").strip()
    if not j:
        return "—"
    rel = _hub_html_esc(_hub_relpath_from_hub_dir(j).replace("\\", "/"))
    w = int(thumb)
    st = (
        "display:block;margin:0 auto;vertical-align:middle;"
        "object-fit:cover;object-position:center;border-radius:4px;"
        f"width:{w}px;height:{w}px;max-width:{w}px;max-height:{w}px;flex-shrink:0"
    )
    return f'<img src="{rel}" width="{w}" height="{w}" alt="" loading="lazy" style="{st}" />'


def _hub_note_md_link(path_md: str, title: str) -> str:
    """HTML 表の中では [[wikilink]] が生テキストになるため、Markdown 形式の内部リンクを使う。"""
    rel = _hub_relpath_from_hub_dir(path_md).replace("\\", "/")
    # Obsidian は %2C 等の href を開けないことが多い → 未エンコードを [](<...>) で渡す
    inner = _hub_angle_bracket_path(rel)
    safe = (
        title.replace("\\", "\\\\")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("|", "｜")
    )
    return f"[{safe}](<{inner}>)"


def _hub_pack_version_cell(pack_display: str, version: str) -> str:
    p = _hub_html_esc(pack_display)
    v = _hub_html_esc(version)
    return (
        f'{p}<br><span style="opacity:0.88;font-size:0.9em">{v}</span>'
    )


def write_chart_constant_hub_pages(
    written: list[tuple[str, str, str, str, str, str, bool]],
    chart_constants: dict[str, dict[str, str]],
) -> None:
    """譜面定数を帯（9 / 9+ / …）ごとにまとめ、帯内は 0.1 見出しで区切って書き出す。"""
    fine_buckets = const_hub_fine_bucket_strings()
    hub: dict[str, list[tuple[str, str, str, str, str, str]]] = {
        b: [] for b in fine_buckets
    }
    thumb = CONST_HUB_JACKET_THUMB
    urls_with_alt_variant = {t[0] for t in written if t[6]}
    for url, title, path_md, jacket_vault, pack_display, version, is_alt in written:
        row = chart_constants.get(url) or {}
        for lab, val in row.items():
            lu = str(lab).strip().upper()
            # BYD 別名ノート: 高難易（BYD/ETR）の定数だけ。FTR 等は本編ノート側に寄せる
            if is_alt:
                if lu not in ("BYD", "ETR"):
                    continue
            elif url in urls_with_alt_variant:
                if lu in ("BYD", "ETR"):
                    continue
            fv = parse_chart_const_float(val)
            if fv is None:
                continue
            if fv < CONST_HUB_MIN - 1e-9 or fv > CONST_HUB_MAX + 1e-9:
                continue
            key = f"{round(fv, 1):.1f}"
            if key not in hub:
                continue
            hub[key].append(
                (str(lab), title, path_md, jacket_vault, pack_display, version)
            )
    CONST_HUB_DIR.mkdir(parents=True, exist_ok=True)
    # 旧 9.0.md 形式を削除（統合ページに置き換え）
    for p in CONST_HUB_DIR.iterdir():
        if not p.is_file() or p.suffix.lower() != ".md":
            continue
        if p.name == "_index.md":
            continue
        if re.fullmatch(r"\d+\.\d+\.md", p.name, flags=re.I):
            try:
                p.unlink()
            except OSError:
                pass

    merged = const_hub_merged_pages()
    for stem, label, fine_list in merged:
        body: list[str] = [
            "---",
            f'title: "譜面定数 {label}"',
            "---",
            "",
            f"# 譜面定数 {label}",
            "",
        ]
        for fk in fine_list:
            sub_rows = list(hub.get(fk) or [])
            sub_rows.sort(
                key=lambda t: (
                    parse_song_version_cell(t[5]),
                    str(t[0]),
                    t[1].lower(),
                )
            )
            body.append(f"## {fk}")
            body.append("")
            if not sub_rows:
                body.append("*（該当ノートなし）*")
            else:
                body.append('<div style="overflow-x:auto">')
                body.append("")
                body.append("| ジャケット | 難易 | 曲 | パック・初出 |")
                body.append("| :---: | :--- | :--- | :--- |")
                for lab, title, path_md, jacket_vault, pack_display, version in sub_rows:
                    lab_html = diff_label_html(lab)
                    j_html = _hub_jacket_cell_html(jacket_vault, thumb)
                    name_md = _hub_note_md_link(path_md, title)
                    pv_cell = _hub_pack_version_cell(pack_display, version)
                    body.append(f"| {j_html} | {lab_html} | {name_md} | {pv_cell} |")
                body.append("")
                body.append("</div>")
            body.append("")
        (CONST_HUB_DIR / f"{stem}.md").write_text(
            "\n".join(body) + "\n", encoding="utf-8"
        )

    index_lines = [
        "---",
        'title: "譜面定数 索引"',
        "---",
        "",
        "# 譜面定数 索引",
        "",
        "定数帯ごとの一覧。各ページ内は 0.1 刻みの見出しで区切り、同一定数内は初出バージョン順。",
        "",
        f"- [[{CONST_HUB_VAULT}/8|8]]（8.0〜8.6）",
        f"- [[{CONST_HUB_VAULT}/8+|8+]]（8.7〜8.9）",
        f"- [[{CONST_HUB_VAULT}/9|9]]（9.0〜9.6）",
        f"- [[{CONST_HUB_VAULT}/9+|9+]]（9.7〜9.9）",
        f"- [[{CONST_HUB_VAULT}/10|10]]（10.0〜10.6）",
        f"- [[{CONST_HUB_VAULT}/10+|10+]]（10.7〜10.9）",
        f"- [[{CONST_HUB_VAULT}/11|11]]（11.0〜11.7）",
        f"- [[{CONST_HUB_VAULT}/11+|11+]]（11.8〜11.9）",
        f"- [[{CONST_HUB_VAULT}/12|12]]（12.0）",
        "",
    ]
    (CONST_HUB_DIR / "_index.md").write_text(
        "\n".join(index_lines) + "\n", encoding="utf-8"
    )


def reset_pack_output_keep_assets() -> None:
    """毎回の生成前に pack 出力を最終系のみにする（_assets のみ残し、他はすべて削除）。"""
    OUT_PACK.mkdir(parents=True, exist_ok=True)
    for child in list(OUT_PACK.iterdir()):
        if child.name == "_assets":
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            try:
                child.unlink()
            except OSError:
                pass


def resolve_jacket_cdn_for_rec(
    rec: dict,
    jacket_cache: dict[str, str],
    do_fetch: bool,
    refresh: bool,
    pending_saves: list[int],
    chart_constants: dict[str, dict[str, str]] | None = None,
    wiki_diff_tiers: dict[str, list[str]] | None = None,
) -> None:
    url = rec["url"]
    is_alt = bool(rec.get("is_alt_chart_variant"))
    key = cache_key_alt(url) if is_alt else cache_key_main(url)
    if not do_fetch:
        rec["jacket_cdn"] = jacket_cache.get(key, "") or jacket_cache.get(url, "")
        return
    if not refresh and jacket_cache.get(key):
        rec["jacket_cdn"] = jacket_cache[key]
        return
    try:
        html = fetch_wiki_html_with_retry(url)
        if chart_constants is not None:
            merge_chart_constants_from_html(chart_constants, url, html)
        if wiki_diff_tiers is not None:
            merge_wiki_diff_tiers_from_html(wiki_diff_tiers, url, html)
            reorder_rec_difficulties_from_tiers(
                rec, wiki_diff_tiers, chart_constants
            )
        urls = collect_jacket_urls(html)
        ju = pick_jacket_url(urls, byd_variant=is_alt)
        if ju:
            jacket_cache[key] = ju
            pending_saves[0] += 1
            if pending_saves[0] % 25 == 0:
                save_jacket_cache({k: v for k, v in jacket_cache.items() if v})
        rec["jacket_cdn"] = ju or ""
    except (HTTPError, URLError, OSError, TimeoutError):
        rec["jacket_cdn"] = jacket_cache.get(key, "")
    time.sleep(0.5)


def main() -> None:
    if not DATA.is_file():
        raise SystemExit(f"データがありません: {DATA}")

    if "--clean" in sys.argv and OUT_PACK.exists():
        shutil.rmtree(OUT_PACK, ignore_errors=True)
        OUT_PACK.mkdir(parents=True, exist_ok=True)
    else:
        reset_pack_output_keep_assets()

    text = DATA.read_text(encoding="utf-8")
    sections = split_pack_sections(text)
    indexed = list(enumerate(sections))
    indexed.sort(
        key=lambda it: (min_pack_version_from_body(it[1][1]), it[0]),
    )

    by_pack: dict[str, list[dict]] = {}
    pack_order: list[str] = []
    for _, (pname, body, _) in indexed:
        if pname not in by_pack:
            by_pack[pname] = []
            pack_order.append(pname)
        songs = parse_section_songs(body)
        by_pack[pname].extend(songs)

    for pname in pack_order:
        songs = by_pack[pname]
        songs.sort(
            key=lambda r: (
                parse_song_version_cell(r["version"]),
                r.get("_wiki_row", 0),
            ),
        )

    OUT_PACK.mkdir(parents=True, exist_ok=True)

    do_fetch = "--fetch-jackets" in sys.argv
    refresh = "--refresh-jackets" in sys.argv
    do_fetch_constants = "--fetch-chart-constants" in sys.argv
    refresh_constants = "--refresh-chart-constants" in sys.argv
    refresh_wiki_tiers = "--refresh-wiki-diff-tiers" in sys.argv
    wiki_tiers_mode = (
        "--fetch-wiki-diff-tiers" in sys.argv or refresh_wiki_tiers
    )
    do_download = "--download-jackets" in sys.argv or do_fetch
    jacket_cache = load_jacket_cache()
    for alt_key, alt_url in DEFAULT_ALT_JACKET_OVERRIDES.items():
        if not jacket_cache.get(alt_key):
            jacket_cache[alt_key] = alt_url
    chart_constants = load_chart_constants()
    wiki_diff_tiers = load_wiki_diff_tiers()
    song_meta = load_song_meta() if not refresh else {}

    if do_fetch and refresh:
        song_meta = {}

    if do_fetch_constants or wiki_tiers_mode:
        urls = sorted({r["url"] for pname in pack_order for r in by_pack[pname]})
        fetched_c = 0
        fetched_t = 0
        for url in urls:
            need_c = do_fetch_constants and (
                refresh_constants or not chart_constants.get(url)
            )
            # 譜面定数取得で HTML を取るときは同じレスポンスから列順も埋める（HTTP 1 回で両方）
            need_t = (
                wiki_tiers_mode
                and (refresh_wiki_tiers or url not in wiki_diff_tiers)
            ) or (need_c and url not in wiki_diff_tiers)
            if not need_c and not need_t:
                continue
            try:
                html = fetch_wiki_html_with_retry(url)
                if need_c:
                    merge_chart_constants_from_html(chart_constants, url, html)
                    fetched_c += 1
                if need_t:
                    merge_wiki_diff_tiers_from_html(wiki_diff_tiers, url, html)
                    fetched_t += 1
            except (HTTPError, URLError, OSError, TimeoutError):
                pass
            time.sleep(0.45)
            if fetched_c and fetched_c % 25 == 0:
                save_chart_constants(chart_constants)
                print(
                    f"chart constants: saved progress ({fetched_c} pages)",
                    flush=True,
                )
            if fetched_t and fetched_t % 25 == 0:
                save_wiki_diff_tiers(wiki_diff_tiers)
                print(
                    f"wiki diff tiers: saved progress ({fetched_t} pages)",
                    flush=True,
                )
        if do_fetch_constants:
            save_chart_constants(chart_constants)
            print(
                f"chart constants: done ({fetched_c} pages fetched this run)",
                flush=True,
            )
        save_wiki_diff_tiers(wiki_diff_tiers)
        print(
            f"wiki diff tiers: done ({fetched_t} tier pages this run, "
            f"{len(wiki_diff_tiers)} URLs in cache)",
            flush=True,
        )

    apply_wiki_diff_tiers_to_pack(
        by_pack, pack_order, wiki_diff_tiers, chart_constants
    )

    pending = [0]

    for pname in pack_order:
        by_pack[pname] = expand_subtitle_variants(
            by_pack[pname],
            do_fetch=do_fetch,
            refresh=refresh,
            jacket_cache=jacket_cache,
            song_meta=song_meta,
            chart_constants=chart_constants,
            wiki_diff_tiers=wiki_diff_tiers,
        )

    if do_fetch:
        save_jacket_cache({k: v for k, v in jacket_cache.items() if v})
        save_song_meta(song_meta)

    for pname in pack_order:
        for rec in by_pack[pname]:
            if rec.get("jacket_cdn"):
                continue
            resolve_jacket_cdn_for_rec(
                rec,
                jacket_cache,
                do_fetch,
                refresh,
                pending,
                chart_constants,
                wiki_diff_tiers,
            )

    if do_fetch:
        save_jacket_cache({k: v for k, v in jacket_cache.items() if v})

    if do_fetch or do_fetch_constants or wiki_tiers_mode:
        save_chart_constants(chart_constants)
        save_wiki_diff_tiers(wiki_diff_tiers)

    if not do_fetch:
        for pname in pack_order:
            for rec in by_pack[pname]:
                if (rec.get("jacket_cdn") or "").strip():
                    continue
                url = rec["url"]
                is_alt = bool(rec.get("is_alt_chart_variant"))
                key = cache_key_alt(url) if is_alt else cache_key_main(url)
                rec["jacket_cdn"] = (
                    jacket_cache.get(key, "")
                    or jacket_cache.get(url, "")
                    or (jacket_cache.get(url + "::BYD") if is_alt else "")
                )

    non_empty = [n for n in pack_order if by_pack.get(n)]
    n_packs = len(non_empty)
    width = 3 if n_packs >= 100 else 2
    written_notes: list[tuple[str, str, str, str, str, str, bool]] = []

    for idx, base_name in enumerate(non_empty, start=1):
        pack_dir_name = f"{idx:0{width}d}_{base_name}"
        pdir = OUT_PACK / pack_dir_name
        pdir.mkdir(parents=True, exist_ok=True)
        stem_counts: dict[str, int] = {}
        songs = by_pack[base_name]
        for rec in songs:
            if rec.get("is_alt_chart_variant"):
                fname = filename_from_title(rec["title"])
            else:
                fname = filename_from_url(rec["url"])
            base = fname[:-3] if fname.endswith(".md") else fname
            c = stem_counts.get(base, 0) + 1
            stem_counts[base] = c
            out_name = fname if c == 1 else f"{base}_{c}.md"
            stem = Path(out_name).stem
            cdn = (rec.get("jacket_cdn") or "").strip()
            rec["jacket_vault_path"] = ""
            if cdn and do_download:
                ext = ext_from_cdn_url(cdn)
                img_path = JACKET_DIR / f"{stem}{ext}"
                vault_rel = f"{JACKET_ASSETS_VAULT}/{stem}{ext}"
                existing_rel = jacket_vault_rel_if_exists(stem, ext)
                if existing_rel and not refresh:
                    rec["jacket_vault_path"] = existing_rel
                else:
                    ok = download_jacket_with_retry(cdn, img_path)
                    if ok:
                        rec["jacket_vault_path"] = vault_rel
                    time.sleep(0.12)
            elif cdn and not do_download:
                ext = ext_from_cdn_url(cdn)
                found_rel = jacket_vault_rel_if_exists(stem, ext)
                if found_rel:
                    rec["jacket_vault_path"] = found_rel
                elif rec.get("is_alt_chart_variant"):
                    main_stem = Path(filename_from_url(rec["url"])).stem
                    main_rel = jacket_vault_rel_if_exists(main_stem, ext)
                    main_cdn = (
                        jacket_cache.get(cache_key_main(rec["url"]), "")
                        or jacket_cache.get(rec["url"], "")
                    ).strip()
                    if main_rel and main_cdn and cdn == main_cdn:
                        rec["jacket_vault_path"] = main_rel

            (pdir / out_name).write_text(
                song_markdown(rec, pack_dir_name, chart_constants),
                encoding="utf-8",
            )
            written_notes.append(
                (
                    rec["url"],
                    rec["title"],
                    f"{PACK_BASE}/{pack_dir_name}/{out_name}",
                    (rec.get("jacket_vault_path") or "").strip(),
                    pack_display_name(pack_dir_name),
                    rec["version"],
                    bool(rec.get("is_alt_chart_variant")),
                )
            )

    write_chart_constant_hub_pages(written_notes, chart_constants)

    total = sum(len(by_pack[n]) for n in non_empty)
    with_j = sum(
        1
        for n in non_empty
        for r in by_pack[n]
        if (r.get("jacket_vault_path") or "").strip()
    )
    print(
        f"OK: packs={n_packs} songs={total} local_jackets={with_j} -> {OUT_PACK} ; "
        f"chart hub -> {CONST_HUB_DIR}"
    )


if __name__ == "__main__":
    main()
