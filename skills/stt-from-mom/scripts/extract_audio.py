from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg

DEFAULT_OUTPUT_BASE_DIR = Path("G:/\ub0b4 \ub4dc\ub77c\uc774\ube0c/Infineon")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract audio from MP4 files using ffmpeg."
    )
    input_group = parser.add_mutually_exclusive_group()
    input_group.add_argument(
        "--input-file",
        type=Path,
        default=None,
        help="Single MP4 file to process.",
    )
    input_group.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="Folder containing MP4 files (default: current directory).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help=(
            "Folder where extracted audio files are written. "
            "If omitted with --input-file, outputs to G:/내 드라이브/Infineon/<input file name>. "
            "Otherwise defaults to ./audio."
        ),
    )
    parser.add_argument(
        "--ext",
        choices=["mp3", "wav", "m4a"],
        default="mp3",
        help="Output audio format (default: mp3).",
    )
    parser.add_argument(
        "--bitrate",
        default="192k",
        help="Audio bitrate for lossy formats (default: 192k).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing output files.",
    )
    return parser.parse_args()


def codec_args(ext: str, bitrate: str) -> list[str]:
    if ext == "mp3":
        return ["-c:a", "libmp3lame", "-b:a", bitrate]
    if ext == "m4a":
        return ["-c:a", "aac", "-b:a", bitrate]
    if ext == "wav":
        return ["-c:a", "pcm_s16le"]
    raise ValueError(f"Unsupported extension: {ext}")


def extract_one(ffmpeg_path: str, src: Path, dst: Path, ext: str, bitrate: str, overwrite: bool) -> int:
    dst.parent.mkdir(parents=True, exist_ok=True)
    overwrite_flag = "-y" if overwrite else "-n"

    cmd = [
        ffmpeg_path,
        "-hide_banner",
        "-loglevel",
        "error",
        overwrite_flag,
        "-i",
        str(src),
        "-vn",
        *codec_args(ext, bitrate),
        str(dst),
    ]

    completed = subprocess.run(cmd, capture_output=True, text=True)
    if completed.returncode != 0 and ext == "mp3":
        # Some ffmpeg builds may not include libmp3lame.
        fallback = dst.with_suffix(".wav")
        fallback_cmd = [
            ffmpeg_path,
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
        fallback_completed = subprocess.run(fallback_cmd, capture_output=True, text=True)
        if fallback_completed.returncode == 0:
            print(f"[OK] {src.name} -> {fallback.name} (mp3 fallback)")
            return 0
        print(f"[FAIL] {src.name}: {completed.stderr.strip() or completed.stdout.strip()}")
        print(f"[FAIL] Fallback wav also failed: {fallback_completed.stderr.strip() or fallback_completed.stdout.strip()}")
        return fallback_completed.returncode

    if completed.returncode != 0:
        print(f"[FAIL] {src.name}: {completed.stderr.strip() or completed.stdout.strip()}")
        return completed.returncode

    print(f"[OK] {src.name} -> {dst.name}")
    return 0


def main() -> int:
    args = parse_args()
    mp4_files: list[Path]

    if args.input_file is not None:
        src = args.input_file.expanduser().resolve()
        if not src.exists() or not src.is_file():
            print(f"Input file does not exist: {src}", file=sys.stderr)
            return 2
        if src.suffix.lower() != ".mp4":
            print(f"Input file must be an MP4: {src}", file=sys.stderr)
            return 2
        mp4_files = [src]
        output_dir = args.output_dir.resolve() if args.output_dir else (DEFAULT_OUTPUT_BASE_DIR / src.stem)
    else:
        input_dir = (args.input_dir or Path.cwd()).expanduser().resolve()
        if not input_dir.exists() or not input_dir.is_dir():
            print(f"Input directory does not exist: {input_dir}", file=sys.stderr)
            return 2

        mp4_files = sorted(input_dir.glob("*.mp4"))
        if not mp4_files:
            print(f"No MP4 files found in {input_dir}")
            return 1

        output_dir = args.output_dir.resolve() if args.output_dir else (Path.cwd() / "audio").resolve()

    ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    failures = 0

    for src in mp4_files:
        dst = output_dir / f"{src.stem}.{args.ext}"
        if dst.exists() and not args.overwrite:
            print(f"[SKIP] {dst.name} already exists (use --overwrite)")
            continue
        rc = extract_one(
            ffmpeg_path=ffmpeg_path,
            src=src,
            dst=dst,
            ext=args.ext,
            bitrate=args.bitrate,
            overwrite=args.overwrite,
        )
        if rc != 0:
            failures += 1

    if failures:
        print(f"Done with failures: {failures}")
        return 1

    print("Done. All files processed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
