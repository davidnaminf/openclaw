#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fcntl
import importlib
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SUPPORTED_INPUT_EXTS = {".mp4", ".mp3", ".wav", ".m4a"}
SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_CONFIG_PATH = SKILL_DIR / "config" / "default.json"
TRANSCRIBE_SCRIPT = SCRIPT_DIR / "transcribe_audio.py"


@dataclass
class RuntimeConfig:
    inbox_dir: Path
    processed_input_dir: Path
    failed_input_dir: Path
    processed_output_dir: Path
    lock_file: Path
    stability_seconds: int
    audio_bitrate: str
    video_max_width: int
    video_fps: int
    video_crf: int
    video_preset: str
    video_audio_kbps: int
    language: str
    speech_model: str
    summary_mode: str
    mom_detail_ko: str
    mom_detail_en: str
    llm_provider: str
    llm_model: str
    openclaw_bin: str
    openclaw_agent_id: str
    openclaw_session_prefix: str
    llm_thinking: str
    meeting_english_vault_dir: Path | None
    meeting_english_expressions_dir: Path | None
    mom_vault_dir: Path | None
    no_meeting_english_skip_checked: bool
    meeting_english_min_difficulty: int
    disable_extra_copies: bool
    no_speaker_labels: bool
    overwrite: bool
    custom_spelling_file: Path | None


def log(level: str, message: str) -> None:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] [{level}] {message}")


def read_launchctl_env(var_name: str) -> str:
    try:
        completed = subprocess.run(
            ["launchctl", "getenv", var_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return ""

    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def hydrate_env_from_launchctl(var_names: list[str]) -> None:
    loaded: list[str] = []
    for name in var_names:
        if os.environ.get(name):
            continue
        value = read_launchctl_env(name)
        if value:
            os.environ[name] = value
            loaded.append(name)
    if loaded:
        log("INFO", f"Loaded env from launchctl: {', '.join(loaded)}")


def load_openai_key_from_auth_profiles() -> None:
    if os.environ.get("OPENAI_API_KEY"):
        return

    state_dir = Path(os.environ.get("OPENCLAW_STATE_DIR", "~/.openclaw")).expanduser()
    auth_store = state_dir / "agents" / "main" / "agent" / "auth-profiles.json"
    if not auth_store.exists():
        return

    try:
        parsed = json.loads(auth_store.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return

    profiles = parsed.get("profiles")
    if not isinstance(profiles, dict):
        return

    ordered_items: list[tuple[str, Any]] = []
    if "openai:default" in profiles:
        ordered_items.append(("openai:default", profiles.get("openai:default")))
    for profile_id, profile in profiles.items():
        if profile_id == "openai:default":
            continue
        ordered_items.append((profile_id, profile))

    for profile_id, profile in ordered_items:
        if not isinstance(profile, dict):
            continue
        if str(profile.get("provider", "")).strip() != "openai":
            continue
        if str(profile.get("type", "")).strip() != "api_key":
            continue

        key = str(profile.get("key", "")).strip()
        if key:
            os.environ["OPENAI_API_KEY"] = key
            log("INFO", f"Loaded OPENAI_API_KEY from OpenClaw auth profile: {profile_id}")
            return

        key_ref = profile.get("keyRef")
        if isinstance(key_ref, dict):
            env_id = str(key_ref.get("id", "")).strip()
            source = str(key_ref.get("source", "")).strip()
            if source == "env" and env_id:
                ref_value = os.environ.get(env_id, "") or read_launchctl_env(env_id)
                if ref_value:
                    os.environ[env_id] = ref_value
                    if env_id == "OPENAI_API_KEY":
                        log("INFO", f"Loaded OPENAI_API_KEY from keyRef env: {env_id}")
                    else:
                        os.environ["OPENAI_API_KEY"] = ref_value
                        log("INFO", f"Loaded OPENAI_API_KEY via keyRef env alias: {env_id}")
                    return


def ensure_module(module_name: str, package_name: str) -> None:
    try:
        importlib.import_module(module_name)
        return
    except ImportError:
        pass

    log("INFO", f"Installing missing dependency: {package_name}")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", package_name])
    importlib.import_module(module_name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Process media files from _inbox and generate compressed mp4/mp3/transcript outputs in Processed/<stem>."
        )
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"Path to JSON config (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Max files to process this run. 0 means no limit.",
    )
    parser.add_argument(
        "--stability-seconds",
        type=int,
        default=None,
        help="Override file stability wait in seconds.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions but do not run ffmpeg/transcription or move files.",
    )
    return parser.parse_args()


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(raw_value: str, config_dir: Path) -> Path:
    path = Path(raw_value).expanduser()
    if not path.is_absolute():
        path = (config_dir / path).resolve()
    return path


def detect_infineon_root() -> Path | None:
    env_value = os.environ.get("STT_FROM_MOM_INFINEON_ROOT", "").strip()
    if env_value:
        env_path = Path(env_value).expanduser()
        if env_path.is_dir():
            return env_path.resolve()

    cloud_root = Path.home() / "Library" / "CloudStorage"
    if not cloud_root.is_dir():
        return None

    candidates: list[Path] = []
    for drive in cloud_root.glob("GoogleDrive-*"):
        candidates.extend(
            [
                drive / "My Drive" / "Infineon",
                drive / "내 드라이브" / "Infineon",
                drive / "Infineon",
            ]
        )

    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    return None


def build_runtime_config(raw_cfg: dict[str, Any], config_path: Path, cli: argparse.Namespace) -> RuntimeConfig:
    cfg_dir = config_path.parent
    root_raw = str(raw_cfg.get("infineon_root", "")).strip()

    if root_raw:
        infineon_root = resolve_path(root_raw, cfg_dir)
    else:
        detected = detect_infineon_root()
        if detected is None:
            raise RuntimeError(
                "Could not detect Infineon root. Set config.infineon_root or STT_FROM_MOM_INFINEON_ROOT."
            )
        infineon_root = detected

    inbox_dir = resolve_path(raw_cfg["inbox_dir"], cfg_dir) if str(raw_cfg.get("inbox_dir", "")).strip() else (infineon_root / "_inbox")
    processed_input_dir = (
        resolve_path(raw_cfg["processed_input_dir"], cfg_dir)
        if str(raw_cfg.get("processed_input_dir", "")).strip()
        else (inbox_dir / "_processed")
    )
    failed_input_dir = (
        resolve_path(raw_cfg["failed_input_dir"], cfg_dir)
        if str(raw_cfg.get("failed_input_dir", "")).strip()
        else (inbox_dir / "_failed")
    )
    processed_output_dir = (
        resolve_path(raw_cfg["processed_output_dir"], cfg_dir)
        if str(raw_cfg.get("processed_output_dir", "")).strip()
        else (infineon_root / "Processed")
    )
    lock_file = (
        resolve_path(raw_cfg["lock_file"], cfg_dir)
        if str(raw_cfg.get("lock_file", "")).strip()
        else (inbox_dir / ".stt_from_mom.lock")
    )

    stability_seconds = int(raw_cfg.get("stability_seconds", 10))
    if cli.stability_seconds is not None:
        stability_seconds = cli.stability_seconds

    custom_spelling_raw = str(raw_cfg.get("custom_spelling_file", "")).strip()
    custom_spelling_file: Path | None = None
    if custom_spelling_raw:
        custom_spelling_file = resolve_path(custom_spelling_raw, cfg_dir)

    meeting_english_vault_raw = str(raw_cfg.get("meeting_english_vault_dir", "")).strip()
    meeting_english_expressions_raw = str(raw_cfg.get("meeting_english_expressions_dir", "")).strip()
    mom_vault_raw = str(raw_cfg.get("mom_vault_dir", "")).strip()

    meeting_english_vault_dir = (
        resolve_path(meeting_english_vault_raw, cfg_dir) if meeting_english_vault_raw else None
    )
    meeting_english_expressions_dir = (
        resolve_path(meeting_english_expressions_raw, cfg_dir) if meeting_english_expressions_raw else None
    )
    mom_vault_dir = resolve_path(mom_vault_raw, cfg_dir) if mom_vault_raw else None

    return RuntimeConfig(
        inbox_dir=inbox_dir,
        processed_input_dir=processed_input_dir,
        failed_input_dir=failed_input_dir,
        processed_output_dir=processed_output_dir,
        lock_file=lock_file,
        stability_seconds=stability_seconds,
        audio_bitrate=str(raw_cfg.get("audio_bitrate", "96k")),
        video_max_width=int(raw_cfg.get("video_max_width", 854)),
        video_fps=int(raw_cfg.get("video_fps", 15)),
        video_crf=int(raw_cfg.get("video_crf", 32)),
        video_preset=str(raw_cfg.get("video_preset", "veryfast")),
        video_audio_kbps=int(raw_cfg.get("video_audio_kbps", 64)),
        language=str(raw_cfg.get("language", "auto")),
        speech_model=str(raw_cfg.get("speech_model", "universal-3-pro")),
        summary_mode=str(raw_cfg.get("summary_mode", "auto")),
        mom_detail_ko=str(raw_cfg.get("mom_detail_ko", "normal")),
        mom_detail_en=str(raw_cfg.get("mom_detail_en", "normal")),
        llm_provider=str(raw_cfg.get("llm_provider", "openclaw-agent")),
        llm_model=str(raw_cfg.get("llm_model", "openai-codex/gpt-5.2")),
        openclaw_bin=str(raw_cfg.get("openclaw_bin", "openclaw")),
        openclaw_agent_id=str(raw_cfg.get("openclaw_agent_id", "main")),
        openclaw_session_prefix=str(raw_cfg.get("openclaw_session_prefix", "stt-from-mom")),
        llm_thinking=str(raw_cfg.get("llm_thinking", "low")),
        meeting_english_vault_dir=meeting_english_vault_dir,
        meeting_english_expressions_dir=meeting_english_expressions_dir,
        mom_vault_dir=mom_vault_dir,
        no_meeting_english_skip_checked=bool(raw_cfg.get("no_meeting_english_skip_checked", False)),
        meeting_english_min_difficulty=int(raw_cfg.get("meeting_english_min_difficulty", 4)),
        disable_extra_copies=bool(raw_cfg.get("disable_extra_copies", True)),
        no_speaker_labels=bool(raw_cfg.get("no_speaker_labels", False)),
        overwrite=bool(raw_cfg.get("overwrite", True)),
        custom_spelling_file=custom_spelling_file,
    )


def ensure_directories(cfg: RuntimeConfig) -> None:
    cfg.inbox_dir.mkdir(parents=True, exist_ok=True)
    cfg.processed_input_dir.mkdir(parents=True, exist_ok=True)
    cfg.failed_input_dir.mkdir(parents=True, exist_ok=True)
    cfg.processed_output_dir.mkdir(parents=True, exist_ok=True)


def acquire_lock(lock_file: Path):
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_file, "a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


def release_lock(handle) -> None:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    finally:
        handle.close()


def list_candidates(inbox_dir: Path) -> list[Path]:
    files = []
    for path in inbox_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() in SUPPORTED_INPUT_EXTS:
            files.append(path)
    return sorted(files, key=lambda p: (p.stat().st_mtime, p.name.lower()))


def is_stable(path: Path, wait_seconds: int) -> bool:
    if not path.exists():
        return False

    stat1 = path.stat()
    time.sleep(wait_seconds)
    if not path.exists():
        return False
    stat2 = path.stat()
    if stat1.st_size != stat2.st_size:
        return False

    try:
        with open(path, "rb"):
            return True
    except OSError:
        return False


def unique_path(directory: Path, filename: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    base = Path(filename).stem
    ext = Path(filename).suffix
    candidate = directory / filename
    if not candidate.exists():
        return candidate

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    idx = 1
    while True:
        candidate = directory / f"{base}_{stamp}_{idx}{ext}"
        if not candidate.exists():
            return candidate
        idx += 1


def allocate_output_dir(root: Path, stem: str) -> Path:
    candidate = root / stem
    if not candidate.exists():
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    idx = 1
    while True:
        candidate = root / f"{stem}_{stamp}_{idx}"
        if not candidate.exists():
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        idx += 1


def run_command(cmd: list[str], dry_run: bool) -> None:
    joined = " ".join(cmd)
    if dry_run:
        log("DRY", joined)
        return

    log("RUN", joined)
    subprocess.check_call(cmd)


def ffmpeg_exe() -> str:
    import imageio_ffmpeg  # noqa: PLC0415

    return imageio_ffmpeg.get_ffmpeg_exe()


def compress_mp4(src: Path, dst: Path, cfg: RuntimeConfig, dry_run: bool) -> None:
    overwrite_flag = "-y" if cfg.overwrite else "-n"
    vf = f"fps={cfg.video_fps},scale='min({cfg.video_max_width},iw)':-2"
    ffmpeg_bin = "ffmpeg" if dry_run else ffmpeg_exe()
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        overwrite_flag,
        "-i",
        str(src),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        cfg.video_preset,
        "-crf",
        str(cfg.video_crf),
        "-movflags",
        "+faststart",
        "-c:a",
        "aac",
        "-b:a",
        f"{cfg.video_audio_kbps}k",
        str(dst),
    ]
    run_command(cmd, dry_run=dry_run)


def extract_mp3(src: Path, dst: Path, cfg: RuntimeConfig, dry_run: bool) -> Path:
    overwrite_flag = "-y" if cfg.overwrite else "-n"
    ffmpeg_bin = "ffmpeg" if dry_run else ffmpeg_exe()
    cmd = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        overwrite_flag,
        "-i",
        str(src),
        "-vn",
        "-c:a",
        "libmp3lame",
        "-b:a",
        cfg.audio_bitrate,
        str(dst),
    ]

    if dry_run:
        run_command(cmd, dry_run=True)
        return dst

    try:
        run_command(cmd, dry_run=False)
        return dst
    except subprocess.CalledProcessError:
        fallback = dst.with_suffix(".wav")
        fallback_cmd = [
            ffmpeg_bin,
            "-hide_banner",
            "-loglevel",
            "error",
            overwrite_flag,
            "-i",
            str(src),
            "-vn",
            "-c:a",
            "pcm_s16le",
            str(fallback),
        ]
        log("WARN", "MP3 extraction failed; retrying as wav fallback")
        run_command(fallback_cmd, dry_run=False)
        return fallback


def build_transcribe_command(audio_file: Path, output_dir: Path, cfg: RuntimeConfig) -> list[str]:
    cmd = [
        sys.executable,
        str(TRANSCRIBE_SCRIPT),
        "--input-file",
        str(audio_file),
        "--output-dir",
        str(output_dir),
        "--language",
        cfg.language,
        "--speech-model",
        cfg.speech_model,
        "--summary-mode",
        cfg.summary_mode,
        "--mom-detail-ko",
        cfg.mom_detail_ko,
        "--mom-detail-en",
        cfg.mom_detail_en,
        "--llm-model",
        cfg.llm_model,
        "--llm-provider",
        cfg.llm_provider,
        "--openclaw-bin",
        cfg.openclaw_bin,
        "--openclaw-agent-id",
        cfg.openclaw_agent_id,
        "--openclaw-session-prefix",
        cfg.openclaw_session_prefix,
        "--llm-thinking",
        cfg.llm_thinking,
        "--meeting-english-min-difficulty",
        str(cfg.meeting_english_min_difficulty),
    ]
    if cfg.no_speaker_labels:
        cmd.append("--no-speaker-labels")
    if cfg.meeting_english_vault_dir:
        cmd.extend(["--meeting-english-vault-dir", str(cfg.meeting_english_vault_dir)])
    if cfg.meeting_english_expressions_dir:
        cmd.extend(["--meeting-english-expressions-dir", str(cfg.meeting_english_expressions_dir)])
    if cfg.mom_vault_dir:
        cmd.extend(["--mom-vault-dir", str(cfg.mom_vault_dir)])
    if cfg.no_meeting_english_skip_checked:
        cmd.append("--no-meeting-english-skip-checked")
    if cfg.disable_extra_copies:
        cmd.extend(
            [
                "--no-meeting-english-vault-copy",
                "--no-meeting-english-expressions-copy",
                "--no-mom-vault-copy",
            ]
        )
    if cfg.custom_spelling_file and cfg.custom_spelling_file.exists():
        cmd.extend(["--custom-spelling-file", str(cfg.custom_spelling_file)])
    if cfg.overwrite:
        cmd.append("--overwrite")
    return cmd


def process_one(path: Path, cfg: RuntimeConfig, dry_run: bool) -> None:
    stem = path.stem
    output_dir = allocate_output_dir(cfg.processed_output_dir, stem)
    log("INFO", f"Output directory: {output_dir}")

    if path.suffix.lower() == ".mp4":
        compressed_mp4 = output_dir / f"{stem}.mp4"
        compress_mp4(path, compressed_mp4, cfg, dry_run=dry_run)
        audio_input = extract_mp3(path, output_dir / f"{stem}.mp3", cfg, dry_run=dry_run)
    else:
        copied = output_dir / path.name
        if dry_run:
            log("DRY", f"copy {path} -> {copied}")
        else:
            shutil.copy2(path, copied)
        audio_input = copied

    transcribe_cmd = build_transcribe_command(audio_input, output_dir, cfg)
    run_command(transcribe_cmd, dry_run=dry_run)


def move_input(path: Path, destination_dir: Path, dry_run: bool) -> None:
    destination = unique_path(destination_dir, path.name)
    if dry_run:
        log("DRY", f"move {path} -> {destination}")
        return
    shutil.move(str(path), str(destination))
    log("INFO", f"Moved input to: {destination}")


def main() -> int:
    args = parse_args()
    config_path = args.config.expanduser().resolve()
    if not TRANSCRIBE_SCRIPT.exists():
        log("ERROR", f"Missing required script: {TRANSCRIBE_SCRIPT}")
        return 2

    # Keep runtime credentials in sync with the desktop LaunchAgent environment
    # and OpenClaw auth profiles, even when this script runs from a clean shell.
    hydrate_env_from_launchctl(["OPENAI_API_KEY", "ASSEMBLYAI_API_KEY", "OPENAI_BASE_URL"])
    load_openai_key_from_auth_profiles()

    raw_cfg = load_json(config_path)
    cfg = build_runtime_config(raw_cfg, config_path, args)
    ensure_directories(cfg)

    if not os.environ.get("ASSEMBLYAI_API_KEY"):
        log("WARN", "ASSEMBLYAI_API_KEY is not set. Transcription will fail.")
    if cfg.llm_provider == "openai-api" and cfg.summary_mode in {"auto", "llm"} and not os.environ.get(
        "OPENAI_API_KEY"
    ):
        log("WARN", "OPENAI_API_KEY is not set. MoM generation will fail with llm_provider=openai-api.")

    lock_handle = acquire_lock(cfg.lock_file)
    if lock_handle is None:
        log("INFO", f"Another run is in progress (lock: {cfg.lock_file})")
        return 0

    failures = 0
    processed = 0
    try:
        if not args.dry_run:
            ensure_module("imageio_ffmpeg", "imageio-ffmpeg")
            ensure_module("assemblyai", "assemblyai")

        candidates = list_candidates(cfg.inbox_dir)
        if args.limit > 0:
            candidates = candidates[: args.limit]

        if not candidates:
            log("INFO", f"No pending media files in {cfg.inbox_dir}")
            return 0

        log("INFO", f"Pending files: {len(candidates)}")
        for src in candidates:
            if not is_stable(src, cfg.stability_seconds):
                log("INFO", f"Skipping unstable file: {src.name}")
                continue

            try:
                log("INFO", f"Processing: {src.name}")
                process_one(src, cfg, dry_run=args.dry_run)
                move_input(src, cfg.processed_input_dir, dry_run=args.dry_run)
                processed += 1
            except Exception as exc:  # noqa: BLE001
                failures += 1
                log("ERROR", f"{src.name} failed: {exc}")
                try:
                    move_input(src, cfg.failed_input_dir, dry_run=args.dry_run)
                except Exception as move_exc:  # noqa: BLE001
                    log("ERROR", f"Failed to move failed input {src.name}: {move_exc}")

        log("INFO", f"Done. processed={processed} failures={failures}")
        return 1 if failures else 0
    finally:
        release_lock(lock_handle)


if __name__ == "__main__":
    raise SystemExit(main())
