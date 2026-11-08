#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用 FFmpeg 编码视频（H.264 / H.265）。

支持三种输入：
  1. 普通视频文件（重新编码）
  2. 图片序列（如 frame_%06d.png）
  3. 原始 YUV / RGB 像素文件（需要 --width、--height）

脚本只依赖 Python 标准库和 PATH 中的 ffmpeg / ffprobe，不需要安装 ffmpeg-python。

示例：
  # 视频转码为 H.264 + AAC MP4
  python encode_video.py input.mp4 output.mp4

  # 30 fps 图片序列编码为视频
  python encode_video.py "frames/frame_%06d.png" output.mp4 --fps 30

  # 1920x1080、yuv420p 的原始帧编码为视频
  python encode_video.py input.yuv output.mp4 --input-type rawvideo ^
      --width 1920 --height 1080 --raw-pix-fmt yuv420p --fps 30

  # 高质量、慢速编码
  python encode_video.py input.mp4 output.mp4 --crf 18 --preset slow

注意：Windows 的 cmd 换行符是 ^，PowerShell 是 `。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, NoReturn

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")

PRESETS = (
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
    "placebo",
)
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
RAW_SUFFIXES = {".yuv", ".raw", ".rgb", ".rgba", ".gray", ".nv12"}
OUTPUT_SUFFIXES = {".mp4", ".mov", ".m4v", ".mkv"}
INPUT_TYPE_LABELS = {
    "video": "普通视频",
    "images": "图片序列",
    "rawvideo": "原始像素",
}


def configure_console_utf8() -> None:
    """避免 Windows PowerShell 捕获输出时按 GBK 解码 UTF-8 源码中的中文。"""
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


configure_console_utf8()


def fail(message: str, code: int = 2) -> NoReturn:
    print(f"错误: {message}", file=sys.stderr)
    raise SystemExit(code)


def format_number(value: float) -> str:
    """给 ffmpeg 输出紧凑但精确的数字，例如 30、29.97。"""
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def format_bytes(size: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(size)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{size} B"


def format_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return "未知"
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"
    return f"{minutes:02d}:{secs:02d}.{millis:03d}"


def format_bitrate(bits_per_second: float | None) -> str:
    if not bits_per_second or bits_per_second <= 0:
        return "未知"
    if bits_per_second >= 1_000_000:
        return f"{bits_per_second / 1_000_000:.2f} Mbps"
    return f"{bits_per_second / 1000:.1f} kbps"


def parse_rate(rate: Any) -> float | None:
    """把 ffprobe 的 '30000/1001' 或 '30' 转为浮点帧率。"""
    if not rate or rate == "N/A":
        return None
    try:
        if isinstance(rate, (int, float)):
            value = float(rate)
        else:
            text = str(rate).strip()
            if "/" in text:
                numerator, denominator = text.split("/", 1)
                value = float(numerator) / float(denominator)
            else:
                value = float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return value if value > 0 else None


def run_ffprobe(path: str) -> dict[str, Any] | None:
    """读取媒体信息；ffprobe 不存在或读取失败时返回 None。"""
    if not shutil.which(FFPROBE):
        return None
    command = [
        FFPROBE,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def get_video_stream(info: dict[str, Any] | None) -> dict[str, Any] | None:
    if not info:
        return None
    for stream in info.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    return None


def get_duration(info: dict[str, Any] | None) -> float | None:
    if not info:
        return None
    candidates: list[Any] = []
    fmt = info.get("format", {})
    if isinstance(fmt, dict):
        candidates.append(fmt.get("duration"))
    stream = get_video_stream(info)
    if stream:
        candidates.append(stream.get("duration"))
    for candidate in candidates:
        try:
            value = float(candidate)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def pattern_to_glob(name: str) -> str:
    """把 printf 风格的 %06d / %d 转成 glob 的 *。"""
    return re.sub(r"%0?\d*d", "*", name)


def count_image_sequence(pattern: Path) -> int | None:
    if not pattern.parent.is_dir():
        return None
    try:
        return sum(
            1
            for item in pattern.parent.glob(pattern_to_glob(pattern.name))
            if item.is_file()
        )
    except OSError:
        return None


def detect_input_type(input_path: Path) -> str:
    if "%" in input_path.name:
        return "images"
    if input_path.suffix.lower() in RAW_SUFFIXES:
        return "rawvideo"
    return "video"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="使用 FFmpeg 把视频、图片序列或 RAW 像素编码为 H.264/H.265 视频。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("示例：", 1)[-1],
    )
    parser.add_argument("input", help="输入视频、图片序列模式或 RAW 文件")
    parser.add_argument("output", help="输出文件（.mp4 / .mov / .m4v / .mkv）")

    parser.add_argument(
        "--input-type",
        choices=("auto", "video", "images", "rawvideo"),
        default="auto",
        help="输入类型，默认 auto（按文件名推断）",
    )
    parser.add_argument(
        "--codec",
        choices=("libx264", "libx265"),
        default="libx264",
        help="视频编码器，默认 libx264（H.264）",
    )
    parser.add_argument(
        "--crf",
        type=int,
        default=23,
        help="恒定质量因子，0-51，越小质量越高，默认 23",
    )
    parser.add_argument(
        "--preset",
        choices=PRESETS,
        default="medium",
        help="x264/x265 速度预设，默认 medium",
    )
    parser.add_argument(
        "--pix-fmt",
        default="yuv420p",
        help="输出像素格式，默认 yuv420p（兼容性最好）",
    )
    parser.add_argument(
        "--audio-bitrate",
        default="192k",
        help="AAC 音频码率，默认 192k",
    )
    parser.add_argument("--no-audio", action="store_true", help="输出不包含音频")
    parser.add_argument(
        "--fps",
        type=float,
        default=None,
        help="图片序列/RAW 输入帧率，默认 30；普通视频不需要指定",
    )
    parser.add_argument("--width", type=int, default=None, help="RAW 输入宽度")
    parser.add_argument("--height", type=int, default=None, help="RAW 输入高度")
    parser.add_argument(
        "--raw-pix-fmt",
        default="yuv420p",
        help="RAW 输入的像素格式，默认 yuv420p",
    )
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的输出")
    parser.add_argument("--dry-run", action="store_true", help="只打印 ffmpeg 命令，不执行")
    parser.add_argument("--verbose", action="store_true", help="显示完整 ffmpeg 日志")
    return parser


def validate_args(args: argparse.Namespace) -> tuple[str, float | None]:
    if not 0 <= args.crf <= 51:
        fail("--crf 必须在 0 到 51 之间")
    if args.fps is not None and args.fps <= 0:
        fail("--fps 必须大于 0")
    if args.width is not None and args.width <= 0:
        fail("--width 必须大于 0")
    if args.height is not None and args.height <= 0:
        fail("--height 必须大于 0")

    input_path = Path(args.input)
    input_type = args.input_type
    if input_type == "auto":
        input_type = detect_input_type(input_path)

    if input_type == "images":
        if "%" not in input_path.name:
            fail("图片序列输入的文件名必须包含 %d 或 %06d，例如 frame_%06d.png")
        if not input_path.parent.is_dir():
            fail(f"图片序列所在目录不存在: {input_path.parent}")
    else:
        if not input_path.is_file():
            fail(f"输入文件不存在: {input_path}")

    if input_type == "rawvideo":
        if args.width is None or args.height is None:
            fail("RAW 输入必须同时指定 --width 和 --height")
    elif args.width is not None or args.height is not None:
        fail("--width/--height 只用于 --input-type rawvideo")
    elif args.fps is not None and input_type == "video":
        fail("普通视频已包含帧率；--fps 只用于图片序列或 RAW 输入")

    output_path = Path(args.output)
    if output_path.suffix.lower() not in OUTPUT_SUFFIXES:
        supported = "、".join(sorted(OUTPUT_SUFFIXES))
        fail(f"输出文件扩展名必须是 {supported}")
    if output_path.exists() and output_path.is_dir():
        fail(f"输出路径是目录而不是文件: {output_path}")
    if input_type != "images" and input_path.is_file():
        try:
            same = os.path.normcase(input_path.resolve()) == os.path.normcase(
                output_path.resolve()
            )
        except OSError:
            same = False
        if same:
            fail("输入文件与输出文件不能是同一个文件")
    if output_path.exists() and not args.overwrite:
        fail(f"输出文件已存在: {output_path}（使用 --overwrite 覆盖）")

    fps: float | None = args.fps
    if fps is None and input_type in ("images", "rawvideo"):
        fps = 30.0
    return input_type, fps


def build_command(
    args: argparse.Namespace,
    input_type: str,
    fps: float | None,
    output_path: Path,
) -> list[str]:
    command = [
        FFMPEG,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "info" if args.verbose else "error",
        "-progress",
        "pipe:1",
        "-nostats",
        "-y" if args.overwrite else "-n",
    ]

    if input_type == "images":
        command += ["-framerate", format_number(fps or 30.0), "-i", args.input]
    elif input_type == "rawvideo":
        command += [
            "-f",
            "rawvideo",
            "-pixel_format",
            args.raw_pix_fmt,
            "-video_size",
            f"{args.width}x{args.height}",
            "-framerate",
            format_number(fps or 30.0),
            "-i",
            args.input,
        ]
    else:
        command += ["-i", args.input]

    command += ["-map", "0:v:0"]
    if not args.no_audio:
        command += ["-map", "0:a:0?"]
    command += [
        "-c:v",
        args.codec,
        "-preset",
        args.preset,
        "-crf",
        str(args.crf),
        "-pix_fmt",
        args.pix_fmt,
    ]

    suffix = output_path.suffix.lower()
    if suffix in {".mp4", ".mov", ".m4v"}:
        command += ["-movflags", "+faststart"]
        if args.codec == "libx265":
            # Apple 播放器要求 HEVC 使用 hvc1 标签，而不是默认的 hev1。
            command += ["-tag:v", "hvc1"]

    if args.no_audio:
        command += ["-an"]
    else:
        command += ["-c:a", "aac", "-b:a", args.audio_bitrate]

    command.append(str(output_path))
    return command


def render_command(command: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def run_ffmpeg(
    command: list[str],
    expected_duration: float | None,
    verbose: bool,
    output_path: Path,
) -> float:
    """运行 ffmpeg，解析 -progress 输出；失败时抛出 SystemExit。"""
    started = time.monotonic()
    if not shutil.which(FFMPEG):
        fail(f"找不到 ffmpeg：{FFMPEG}。请安装并加入 PATH，或设置 FFMPEG_BIN。")

    with tempfile.TemporaryFile(mode="w+b") as error_file:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=None if verbose else error_file,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            fail(f"无法启动 ffmpeg: {exc}")

        assert process.stdout is not None
        state: dict[str, str] = {}
        last_tty_length = 0
        last_bucket: int | None = None
        is_tty = sys.stdout.isatty()

        def show_progress() -> None:
            nonlocal last_tty_length, last_bucket
            try:
                frame = int(state.get("frame", "0"))
            except ValueError:
                frame = 0
            try:
                encoded_seconds = int(state.get("out_time_us", "0")) / 1_000_000
            except ValueError:
                encoded_seconds = 0.0
            speed = state.get("speed", "?")

            is_end = state.get("progress") == "end"
            if expected_duration and expected_duration > 0:
                percent = (
                    100.0
                    if is_end
                    else min(99.9, encoded_seconds / expected_duration * 100)
                )
                text = (
                    f"进度 {percent:5.1f}% | 已编码 {format_duration(encoded_seconds)}"
                    f" / {format_duration(expected_duration)} | {frame} 帧 | {speed}"
                )
                bucket = 20 if is_end else int(percent // 5)
            else:
                text = f"已处理 {format_duration(encoded_seconds)} | {frame} 帧 | {speed}"
                bucket = int(encoded_seconds // 2)

            if is_tty:
                padding = " " * max(0, last_tty_length - len(text))
                sys.stdout.write("\r" + text + padding)
                sys.stdout.flush()
                last_tty_length = len(text)
            elif bucket != last_bucket:
                print(text)
                last_bucket = bucket

        cancelled = False
        try:
            for raw_line in process.stdout:
                line = raw_line.strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                state[key] = value
                if key == "progress":
                    show_progress()
            return_code = process.wait()
        except KeyboardInterrupt:
            cancelled = True
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            return_code = 130

        if is_tty and not cancelled:
            sys.stdout.write("\n")
            sys.stdout.flush()

        if cancelled:
            print("已取消编码。", file=sys.stderr)
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            raise SystemExit(130)

        if return_code != 0:
            error_text = ""
            if not verbose:
                error_file.seek(0)
                error_text = error_file.read().decode("utf-8", errors="replace")
            tail = "\n".join(error_text.strip().splitlines()[-25:])
            if tail:
                print(tail, file=sys.stderr)
            print(f"错误: ffmpeg 退出码 {return_code}", file=sys.stderr)
            if output_path.exists():
                try:
                    output_path.unlink()
                except OSError:
                    pass
            raise SystemExit(return_code if return_code > 0 else 1)

    return time.monotonic() - started


def print_output_summary(output_path: Path, elapsed: float) -> None:
    size = output_path.stat().st_size
    info = run_ffprobe(str(output_path))
    duration = get_duration(info)
    video = get_video_stream(info)
    audio = None
    if info:
        for stream in info.get("streams", []):
            if stream.get("codec_type") == "audio":
                audio = stream
                break

    print(f"\n编码完成: {output_path}")
    print(f"文件大小: {format_bytes(size)}")
    if video:
        width = video.get("width", "?")
        height = video.get("height", "?")
        fps = parse_rate(video.get("avg_frame_rate") or video.get("r_frame_rate"))
        fps_text = f"{fps:.2f} fps" if fps else "帧率未知"
        print(
            f"视频流: {video.get('codec_name', '?')} {width}x{height}, "
            f"{fps_text}, {video.get('pix_fmt', '?')}"
        )
    if audio:
        print(
            f"音频流: {audio.get('codec_name', '?')}, "
            f"{audio.get('sample_rate', '?')} Hz, {audio.get('channels', '?')} 声道"
        )
    elif info:
        print("音频流: 无")
    if duration:
        bitrate = size * 8 / duration
        print(f"时长: {format_duration(duration)} | 平均码率: {format_bitrate(bitrate)}")
    print(f"耗时: {elapsed:.2f} 秒")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    input_type, fps = validate_args(args)
    output_path = Path(args.output)

    if input_type == "video":
        source_info = run_ffprobe(args.input)
        expected_duration = get_duration(source_info)
        if source_info and not get_video_stream(source_info):
            fail("输入文件中没有视频流")
    elif input_type == "images":
        frame_count = count_image_sequence(Path(args.input))
        expected_duration = frame_count / fps if frame_count and fps else None
    else:
        expected_duration = None

    command = build_command(args, input_type, fps, output_path)

    print(f"输入 ({INPUT_TYPE_LABELS[input_type]}): {args.input}")
    print(f"输出: {output_path} | {args.codec} | CRF {args.crf} | {args.pix_fmt}")

    if args.dry_run:
        print("\n将执行:")
        print(render_command(command))
        return 0

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        fail(f"无法创建输出目录 {output_path.parent}: {exc}")
    elapsed = run_ffmpeg(command, expected_duration, args.verbose, output_path)
    print_output_summary(output_path, elapsed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
