#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
朗読用テキストを A.I.VOICE Editor API 経由で音声ファイルに保存する。

前提:
  - Windows / A.I.VOICE Editor（API 対応版）インストール済み
  - pip install aivoice-python pythonnet

Editor は起動していなくても API がエディタを起動できる場合があります。
音声保存形式・ファイル命名はエディタ側の設定の影響を受けます。

例:
  python generate_story_tts_aivoice.py --list-presets
  python generate_story_tts_aivoice.py --story Arcaea --section 0-1 --preset-match ゆかり
  python generate_story_tts_aivoice.py --story Arcaea --sections 0-1,0-2,0-3 --expressive
  python generate_story_tts_aivoice.py --batch --dry-run
  python generate_story_tts_aivoice.py --batch
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from story_folder_names import story_subdir_for_read, story_subdir_for_write

ROOT = Path(__file__).resolve().parents[1]
STORY_DIR = ROOT / "story"
TTS_TXT_DIR = STORY_DIR / "_tts"
AUDIO_DIR = STORY_DIR / "_audio"

DEFAULT_EDITOR = r"C:\Program Files\AI\AIVoice\AIVoiceEditor"
DEFAULT_BATCH_MANIFEST = ROOT / "_data" / "story_act1_creation_batch.json"

# ホスト未取得時のマスター初期値（実機例に合わせる）
_FALLBACK_MASTER: dict = {
    "Volume": 1,
    "Speed": 1,
    "Pitch": 1,
    "PitchRange": 1,
    "MiddlePause": 150,
    "LongPause": 370,
    "SentencePause": 800,
}

# 朗読向け: 抑揚をやや強め、話速わずかに落とし、句読点間の余白を少し伸ばす
_EXPRESSIVE_MASTER: dict = {
    "PitchRange": 1.22,
    "Speed": 0.93,
    "SentencePause": 980,
    "LongPause": 460,
    "MiddlePause": 165,
}


def _pick_preset(presets: list[str], match: str) -> str:
    m = match.strip()
    if not m:
        return presets[0] if presets else ""
    lower = m.lower()
    for p in presets:
        if m == p:
            return p
    for p in presets:
        if lower in p.lower():
            return p
    return ""


def _parse_master(raw: str) -> dict:
    d = dict(_FALLBACK_MASTER)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            d.update(parsed)
    except Exception:
        pass
    return d


def _master_to_json(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False, separators=(",", ":"))


def _apply_master_overrides(base: dict, overrides: dict) -> dict:
    m = dict(base)
    for k, v in overrides.items():
        if v is None:
            continue
        if k in ("MiddlePause", "LongPause", "SentencePause"):
            m[k] = int(round(float(v)))
        else:
            m[k] = float(v)
    return m


def _tts_base(story: str) -> Path:
    return story_subdir_for_read(TTS_TXT_DIR, story, ROOT)


def _audio_base_write(story: str) -> Path:
    return story_subdir_for_write(AUDIO_DIR, story, ROOT)


def _load_merged_text(story: str, section_ids: list[str]) -> str:
    base = _tts_base(story)
    parts: list[str] = []
    for sid in section_ids:
        sid = sid.strip()
        if not sid:
            continue
        p = base / f"{sid}.txt"
        if not p.is_file():
            raise SystemExit(f"テキストが見つかりません: {p}")
        parts.append(p.read_text(encoding="utf-8").strip())
    if not parts:
        raise SystemExit("結合するセクションがありません。")
    return "\n\n".join(parts)


def _default_out_path(story: str, section_ids: list[str]) -> Path:
    out_dir = _audio_base_write(story)
    if len(section_ids) == 1:
        return out_dir / f"{section_ids[0]}_aivoice.wav"
    return out_dir / f"{section_ids[0]}_to_{section_ids[-1]}_aivoice.wav"


def _run(
    editor_dir: str,
    text: str,
    out_path: Path,
    preset_match: str,
    list_presets_only: bool,
    master_overrides: dict,
) -> None:
    try:
        from aivoice_python import AIVoiceTTsControl, HostStatus
    except ImportError as e:
        raise SystemExit(
            "aivoice-python が未インストールです。\n"
            "  pip install aivoice-python"
        ) from e

    ctrl = AIVoiceTTsControl(editor_dir=editor_dir)
    hosts = ctrl.get_available_host_names()
    if not hosts:
        raise SystemExit(
            "利用可能な A.I.VOICE ホストがありません。"
            " Editor が正しくインストールされているか確認してください。"
        )

    ctrl.initialize(hosts[0])
    if ctrl.status == HostStatus.NotRunning:
        ctrl.start_host()
    ctrl.connect()

    try:
        presets = ctrl.voice_preset_names
        if list_presets_only:
            for p in presets:
                print(p)
            return

        if not presets:
            raise SystemExit("ボイスプリセットが取得できませんでした（接続後に再試行）。")

        name = _pick_preset(presets, preset_match)
        if not name:
            raise SystemExit(
                f"プリセットが見つかりません: --preset-match {preset_match!r}\n"
                "利用可能な名前は --list-presets で確認してください。"
            )

        ctrl.current_voice_preset_name = name

        mc = _parse_master(ctrl.master_control)
        mc = _apply_master_overrides(mc, master_overrides)
        ctrl.master_control = _master_to_json(mc)

        ctrl.text = text

        out_path.parent.mkdir(parents=True, exist_ok=True)
        ctrl.save_audio_to_file(str(out_path))

        txt_path = out_path.with_suffix(".txt")
        txt_path.write_text(text, encoding="utf-8")

        pr = mc.get("PitchRange")
        sp = mc.get("Speed")
        print(f"OK: preset={name!r} PitchRange={pr} Speed={sp} -> {out_path}")
        print(f"    text: {txt_path}")
    finally:
        try:
            ctrl.disconnect()
        except Exception:
            pass


def _job_expressive(job: dict, defaults: dict, cli_expressive: bool) -> bool:
    if cli_expressive:
        return True
    if "expressive" in job:
        return bool(job["expressive"])
    return bool(defaults.get("expressive", False))


def _build_overrides(
    expressive: bool,
    volume: float | None,
    speed: float | None,
    pitch: float | None,
    pitch_range: float | None,
    middle_pause: int | None,
    long_pause: int | None,
    sentence_pause: int | None,
) -> dict:
    o: dict = {}
    if expressive:
        o.update(_EXPRESSIVE_MASTER)
    if volume is not None:
        o["Volume"] = volume
    if speed is not None:
        o["Speed"] = speed
    if pitch is not None:
        o["Pitch"] = pitch
    if pitch_range is not None:
        o["PitchRange"] = pitch_range
    if middle_pause is not None:
        o["MiddlePause"] = middle_pause
    if long_pause is not None:
        o["LongPause"] = long_pause
    if sentence_pause is not None:
        o["SentencePause"] = sentence_pause
    return o


def _build_overrides_from_args(args: argparse.Namespace) -> dict:
    return _build_overrides(
        bool(args.expressive),
        args.volume,
        args.speed,
        args.pitch,
        args.pitch_range,
        args.middle_pause,
        args.long_pause,
        args.sentence_pause,
    )


def _missing_section_txts(story: str, section_ids: list[str]) -> list[str]:
    base = _tts_base(story)
    return [s for s in section_ids if not (base / f"{s}.txt").is_file()]


def _run_batch(
    manifest_path: Path,
    editor_dir: str,
    args: argparse.Namespace,
) -> None:
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    defaults = raw.get("defaults") or {}
    jobs = raw.get("jobs") or []
    if not jobs:
        raise SystemExit("マニフェストに jobs がありません。")

    for i, job in enumerate(jobs):
        story = (job.get("story") or "").strip()
        sections = job.get("sections")
        if not story or not isinstance(sections, list) or not sections:
            print(f"SKIP [{i + 1}]: invalid job {job!r}")
            continue

        section_ids = [str(s).strip() for s in sections if str(s).strip()]
        missing = _missing_section_txts(story, section_ids)
        if missing:
            print(f"SKIP [{story}]: missing txt → {missing}")
            continue

        preset = (
            (job.get("preset_match") or defaults.get("preset_match") or args.preset_match or "ゆかり").strip()
        )
        expressive = _job_expressive(job, defaults, bool(args.expressive))
        overrides = _build_overrides(
            expressive,
            args.volume,
            args.speed,
            args.pitch,
            args.pitch_range,
            args.middle_pause,
            args.long_pause,
            args.sentence_pause,
        )

        text = _load_merged_text(story, section_ids)
        out_s = (job.get("out") or "").strip()
        if out_s:
            out = Path(out_s)
        else:
            out = _default_out_path(story, section_ids)

        if args.dry_run:
            print(f"DRY-RUN [{story}] {section_ids[0]}..{section_ids[-1]} -> {out}")
            continue

        _run(
            editor_dir,
            text,
            out,
            preset,
            list_presets_only=False,
            master_overrides=overrides,
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="Story TTS via A.I.VOICE Editor API")
    ap.add_argument(
        "--editor-dir",
        default=DEFAULT_EDITOR,
        help="AIVoiceEditor フォルダ（AI.Talk.Editor.Api.dll がある場所）",
    )
    ap.add_argument("--story", default="Arcaea", help="story/_tts 下のフォルダ名")
    ap.add_argument("--section", default="", help="単一セクション例: 0-1（--sections 未指定時）")
    ap.add_argument(
        "--sections",
        default="",
        help="カンマ区切りで複数結合（例: 0-1,0-2,0-3）。指定時は --section より優先",
    )
    ap.add_argument(
        "--text-file",
        default="",
        help="txt の直接指定（指定時は --story/--section/--sections を無視）",
    )
    ap.add_argument(
        "--preset-match",
        default="ゆかり",
        help="ボイスプリセット名の部分一致（空なら先頭のプリセット）",
    )
    ap.add_argument(
        "--out",
        default="",
        help="出力 wav パス（未指定時はセクションに応じた既定名）",
    )
    ap.add_argument(
        "--list-presets",
        action="store_true",
        help="接続してボイスプリセット一覧を表示して終了",
    )
    ap.add_argument(
        "--expressive",
        action="store_true",
        help="朗読向けに抑揚・話速・ポーズをやや強調（ホスト値に上書きマージ）",
    )
    ap.add_argument("--volume", type=float, default=None, help="マスター Volume")
    ap.add_argument("--speed", type=float, default=None, help="マスター Speed（話速）")
    ap.add_argument("--pitch", type=float, default=None, help="マスター Pitch")
    ap.add_argument("--pitch-range", type=float, default=None, help="マスター PitchRange（抑揚）")
    ap.add_argument("--middle-pause", type=int, default=None, help="ミリ秒")
    ap.add_argument("--long-pause", type=int, default=None, help="ミリ秒")
    ap.add_argument("--sentence-pause", type=int, default=None, help="ミリ秒")
    ap.add_argument(
        "--batch",
        action="store_true",
        help="マニフェストに従い Main Story Act I 等を連結出力（txt が揃っているジョブのみ）",
    )
    ap.add_argument(
        "--batch-manifest",
        default="",
        help=f"バッチ用 JSON（省略時: {DEFAULT_BATCH_MANIFEST}）",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="バッチ時: 接続せず実行予定のみ表示",
    )
    args = ap.parse_args()

    if args.batch:
        mp = Path(args.batch_manifest) if args.batch_manifest.strip() else DEFAULT_BATCH_MANIFEST
        if not mp.is_file():
            raise SystemExit(f"マニフェストが見つかりません: {mp}")
        if args.list_presets:
            raise SystemExit("--batch と --list-presets は同時に使えません。")
        _run_batch(mp, args.editor_dir, args)
        return

    overrides = _build_overrides_from_args(args)

    if args.list_presets:
        _run(args.editor_dir, "", Path(), "", list_presets_only=True, master_overrides={})
        return

    if args.text_file:
        tf = Path(args.text_file)
        if not tf.is_file():
            raise SystemExit(f"テキストが見つかりません: {tf}")
        text = tf.read_text(encoding="utf-8")
        if args.out:
            out = Path(args.out)
        else:
            out = _audio_base_write(args.story) / f"{tf.stem}_aivoice.wav"
    else:
        if args.sections.strip():
            section_ids = [s.strip() for s in args.sections.split(",") if s.strip()]
        else:
            sec = args.section.strip() or "0-1"
            section_ids = [sec]
        text = _load_merged_text(args.story, section_ids)
        if args.out:
            out = Path(args.out)
        else:
            out = _default_out_path(args.story, section_ids)

    _run(
        args.editor_dir,
        text,
        out,
        args.preset_match,
        list_presets_only=False,
        master_overrides=overrides,
    )


if __name__ == "__main__":
    main()
