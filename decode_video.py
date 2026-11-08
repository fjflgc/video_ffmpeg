#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用 FFmpeg 解码视频。

支持两种输出：
  1. 图片序列（PNG / JPEG / WebP / BMP ...）
  2. 原始像素文件（RAW YUV / RGB，默认 yuv420p）

脚本只依赖 Python 标准库和 PATH 中的 ffmpeg / ffprobe，不需要安装 ffmpeg-python。

示例：
  # 查看媒体信息
  python decode_video.py input.mp4 --info

  # 解码为 PNG 序列，输出到 frames 目录（frame_000000.png ...）
  python decode_video.py input.mp4 -o frames/

  # 指定文件名模式，并从第 2 秒开始只取 3 秒
  python decode_video.py input.mp4 -o "frames/shot_%06d.png" --start 2 --duration 3

  # 每秒只保留 1 帧
  python decode_video.py input.mp4 -o frames/ --fps 1

  # 解码为 RAW YUV420p，供其他程序直接读取
  python decode_video.py input.mp4 -o decoded.yuv --format raw --pix-fmt yuv420p

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

FFMPEG = os.environ.get("FFMPEG_BIN", "ffmpeg")
FFPROBE = os.environ.get("FFPROBE_BIN", "ffprobe")

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
RAW_SUFFIXES = {".yuv", ".raw", ".rgb", ".rgba", ".gray", ".nv12"}


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


def parse_time(value: str, option: str) -> float:
    """接受秒数或 HH:MM:SS(.mmm) 格式。"""
    text = value.strip()
    try:
        if ":" in text:
            parts = text.split(":")
            if len(parts) > 3 or any(part == "" for part in parts):
                raise ValueError
            seconds = 0.0
            for part in parts:
                seconds = seconds * 60.0 + float(part)
        else:
            seconds = float(text)
    except ValueError:
        fail(f"{option} 格式无效: {value}（可用 2.5 或 00:00:02.500）")
    if seconds < 0:
        fail(f"{option} 不能为负数")
    return seconds


def run_ffprobe(path: str) -> dict[str, Any] | None:
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


def get_frame_count(info: dict[str, Any] | None) -> int | None:
    stream = get_video_stream(info)
    if not stream:
        return None
    try:
        value = int(stream.get("nb_frames"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def pattern_to_glob(name: str) -> str:
    return re.sub(r"%0?\d*d", "*", name)


def find_pattern_files(pattern: Path) -> list[Path]:
    if not pattern.parent.is_dir():
        return []
    try:
        return sorted(
            item
            for item in pattern.parent.glob(pattern_to_glob(pattern.name))
            if item.is_file()
        )
    except OSError:
        return []


@dataclass(frozen=True)
class OutputPlan:
    kind: str  # "images" 或 "raw"
    target: Path
    pattern: Path | None = None


def build_output_plan(output: str, requested_format: str) -> OutputPlan:
    path = Path(output)
    kind = requested_format
    if kind == "auto":
        kind = "raw" if path.suffix.lower() in RAW_SUFFIXES else "images"

    if kind == "raw":
        if path.exists() and path.is_dir():
            fail(f"RAW 输出必须是文件，不能是目录: {path}")
        if "%" in path.name:
            fail("RAW 输出文件名不能包含 %d 模式")
        if path.suffix.lower() in IMAGE_SUFFIXES:
            fail("RAW 输出不应使用图片扩展名；请改用 .yuv/.raw 或指定 --format images")
        if path.name == "-":
            fail("不支持把二进制 RAW 输出到标准输出")
        return OutputPlan(kind="raw", target=path)

    # 图片序列：允许目录、目录/模式、具体文件名三种写法
    if output in ("-", ""):
        fail("图片输出需要目录或文件名模式")
    directory_hint = output.endswith(("/", "\\")) or path.suffix == ""
    if directory_hint:
        if path.exists() and not path.is_dir():
            fail(f"输出路径已存在且不是目录: {path}")
        pattern = path / "frame_%06d.png"
        return OutputPlan(kind="images", target=pattern, pattern=pattern)

    if path.exists() and path.is_dir():
        pattern = path / "frame_%06d.png"
        return OutputPlan(kind="images", target=pattern, pattern=pattern)

    if "%" in path.name:
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            fail("图片序列文件名必须带图片扩展名，例如 frame_%06d.png")
        return OutputPlan(kind="images", target=path, pattern=path)

    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        # 写单个文件会不断覆盖同一帧，因此自动插入 _%06d
        pattern = path.with_name(f"{path.stem}_%06d{path.suffix}")
        return OutputPlan(kind="images", target=pattern, pattern=pattern)

    fail(
        "无法识别图片输出路径。请使用目录（frames/）、含 %06d 的模式"
        "（frames/frame_%06d.png），或 .png/.jpg 文件名。"
    )


def check_output_conflicts(plan: OutputPlan, input_path: Path, overwrite: bool) -> None:
    if plan.kind == "raw":
        try:
            same = os.path.normcase(input_path.resolve()) == os.path.normcase(
                plan.target.resolve()
            )
        except OSError:
            same = False
        if same:
            fail("输入文件与 RAW 输出文件不能是同一个文件")
        if plan.target.exists() and not overwrite:
            fail(f"输出文件已存在: {plan.target}（使用 --overwrite 覆盖）")
        return

    assert plan.pattern is not None
    existing = find_pattern_files(plan.pattern)
    if existing and not overwrite:
        fail(
            f"输出位置已有 {len(existing)} 个匹配文件（如 {existing[0].name}）。"
            "使用 --overwrite 覆盖，或换一个输出目录。"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="使用 FFmpeg 把视频解码为图片序列或 RAW 像素文件。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("示例：", 1)[-1],
    )
    parser.add_argument("input", help="输入视频文件")
    parser.add_argument(
        "-o",
        "--output",
        help="输出目录、图片模式（%%06d）或 RAW 文件；"
        "若给 .png/.jpg 文件名会自动插入 _%%06d",
    )
    parser.add_argument(
        "--format",
        choices=("auto", "images", "raw"),
        default="auto",
        help="输出类型，默认按输出扩展名推断",
    )
    parser.add_argument(
        "--pix-fmt",
        default="yuv420p",
        help="RAW 输出的像素格式，默认 yuv420p",
    )
    parser.add_argument(
        "--fps",
        type=float,
        default=None,
        help="按指定帧率抽帧（如 1 表示每秒 1 帧）；默认保留原帧率",
    )
    parser.add_argument(
        "--start",
        default=None,
        help="起始时间，单位秒或 HH:MM:SS，例如 2.5 / 00:00:02.500",
    )
    parser.add_argument(
        "--duration",
        default=None,
        help="解码时长，单位秒或 HH:MM:SS",
    )
    parser.add_argument("--overwrite", action="store_true", help="允许覆盖已有输出")
    parser.add_argument("--info", action="store_true", help="打印媒体信息；可单独使用")
    parser.add_argument("--dry-run", action="store_true", help="只打印 ffmpeg 命令，不执行")
    parser.add_argument("--verbose", action="store_true", help="显示完整 ffmpeg 日志")
    return parser


def print_media_info(path: str) -> None:
    info = run_ffprobe(path)
    if info is None:
        if not shutil.which(FFPROBE):
            fail(f"找不到 ffprobe：{FFPROBE}。请安装并加入 PATH，或设置 FFPROBE_BIN。")
        fail("无法读取媒体信息，请确认文件未损坏")

    fmt = info.get("format", {})
    if not isinstance(fmt, dict):
        fmt = {}
    print(f"媒体信息: {path}")
    print(f"  容器: {fmt.get('format_long_name') or fmt.get('format_name') or '未知'}")
    print(f"  时长: {format_duration(get_duration(info))}")
    try:
        total_bitrate = float(fmt.get("bit_rate"))
    except (TypeError, ValueError):
        total_bitrate = None
    print(f"  总码率: {format_bitrate(total_bitrate)}")
    try:
        size = int(fmt.get("size"))
    except (TypeError, ValueError):
        size = None
    if size is not None:
        print(f"  文件大小: {format_bytes(size)}")

    streams = info.get("streams", [])
    for index, stream in enumerate(streams):
        stream_index = stream.get("index", index)
        stream_type = stream.get("codec_type", "unknown")
        codec = stream.get("codec_name", "?")
        if stream_type == "video":
            fps = parse_rate(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
            fps_text = f"{fps:.2f} fps" if fps else "帧率未知"
            print(
                f"  #{stream_index} 视频: {codec} "
                f"{stream.get('width', '?')}x{stream.get('height', '?')}, "
                f"{stream.get('pix_fmt', '?')}, {fps_text}"
            )
            frames = stream.get("nb_frames")
            if frames and frames != "N/A":
                print(f"       帧数: {frames}")
        elif stream_type == "audio":
            print(
                f"  #{stream_index} 音频: {codec}, "
                f"{stream.get('sample_rate', '?')} Hz, "
                f"{stream.get('channels', '?')} 声道, "
                f"{stream.get('channel_layout', '未知布局')}"
            )
        elif stream_type == "subtitle":
            print(f"  #{stream_index} 字幕: {codec}")
        else:
            print(f"  #{stream_index} {stream_type}: {codec}")


def build_command(
    args: argparse.Namespace,
    plan: OutputPlan,
    start_seconds: float | None,
    duration_seconds: float | None,
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
    if start_seconds is not None:
        command += ["-ss", format_number(start_seconds)]
    command += ["-i", args.input]
    command += ["-map", "0:v:0", "-an", "-sn", "-dn"]

    if args.fps is not None:
        command += ["-vf", f"fps={format_number(args.fps)}"]
    if duration_seconds is not None:
        command += ["-t", format_number(duration_seconds)]

    if plan.kind == "raw":
        command += [
            "-f",
            "rawvideo",
            "-pix_fmt",
            args.pix_fmt,
            str(plan.target),
        ]
    else:
        command += [
            "-f",
            "image2",
            "-fps_mode",
            "passthrough",
            "-start_number",
            "0",
            str(plan.target),
        ]
    return command


def render_command(command: list[str]) -> str:
    if os.name == "nt":
        return subprocess.list2cmdline(command)
    return shlex.join(command)


def run_ffmpeg(
    command: list[str],
    plan: OutputPlan,
    expected_frames: int | None,
    expected_seconds: float | None,
    verbose: bool,
) -> tuple[float, int | None]:
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
                decoded_seconds = int(state.get("out_time_us", "0")) / 1_000_000
            except ValueError:
                decoded_seconds = 0.0
            speed = state.get("speed", "?")

            is_end = state.get("progress") == "end"
            percent: float | None = None
            if expected_frames and expected_frames > 0:
                percent = (
                    100.0 if is_end else min(99.9, frame / expected_frames * 100)
                )
                text = (
                    f"进度 {percent:5.1f}% | {frame} / {expected_frames} 帧"
                    f" | {format_duration(decoded_seconds)} | {speed}"
                )
                bucket = 20 if is_end else int(percent // 5)
            elif expected_seconds and expected_seconds > 0:
                percent = (
                    100.0
                    if is_end
                    else min(99.9, decoded_seconds / expected_seconds * 100)
                )
                text = (
                    f"进度 {percent:5.1f}% | 已解码 {format_duration(decoded_seconds)}"
                    f" / {format_duration(expected_seconds)} | {frame} 帧 | {speed}"
                )
                bucket = 20 if is_end else int(percent // 5)
            else:
                text = f"已解码 {format_duration(decoded_seconds)} | {frame} 帧 | {speed}"
                bucket = int(decoded_seconds // 2)

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
            print("已取消解码。", file=sys.stderr)
            if plan.kind == "raw" and plan.target.exists():
                try:
                    plan.target.unlink()
                except OSError:
                    pass
            else:
                print("注意：图片序列可能已生成部分文件。", file=sys.stderr)
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
            if plan.kind == "raw" and plan.target.exists():
                try:
                    plan.target.unlink()
                except OSError:
                    pass
            raise SystemExit(return_code if return_code > 0 else 1)

    try:
        decoded_frames = int(state.get("frame", ""))
    except ValueError:
        decoded_frames = None
    return time.monotonic() - started, decoded_frames


def print_decode_summary(
    plan: OutputPlan,
    input_info: dict[str, Any] | None,
    elapsed: float,
    pix_fmt: str,
    decoded_frames: int | None,
) -> None:
    video = get_video_stream(input_info)
    print("\n解码完成")
    if plan.kind == "raw":
        assert plan.target.exists()
        size = plan.target.stat().st_size
        print(f"输出文件: {plan.target}")
        print(f"格式: RAW {pix_fmt} | 大小: {format_bytes(size)}")
        if video:
            print(
                f"源分辨率: {video.get('width', '?')}x{video.get('height', '?')} "
                f"| 输出像素格式: {pix_fmt}"
            )
    else:
        assert plan.pattern is not None
        all_files = find_pattern_files(plan.pattern)
        if decoded_frames is None:
            files = all_files
            frame_count = len(all_files)
        else:
            # image2 从 0 连续编号，按 ffmpeg 实际输出的帧数取本次生成的文件，
            # 避免 --overwrite 时把目录中残留的旧帧也算进去。
            files = all_files[:decoded_frames]
            frame_count = decoded_frames
        total_size = sum(item.stat().st_size for item in files if item.exists())
        print(f"输出目录: {plan.pattern.parent}")
        print(f"命名模式: {plan.pattern.name}")
        print(f"输出帧数: {frame_count} | 总大小: {format_bytes(total_size)}")
        if decoded_frames is not None and len(all_files) > decoded_frames:
            print(
                f"提示: 目录中另有 {len(all_files) - decoded_frames} 个旧帧未被覆盖。",
                file=sys.stderr,
            )
        if frame_count == 0:
            print("警告: 没有生成任何图片，请检查时间参数或输入视频。", file=sys.stderr)
    print(f"耗时: {elapsed:.2f} 秒")


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_file():
        fail(f"输入文件不存在: {input_path}")
    if args.fps is not None and args.fps <= 0:
        fail("--fps 必须大于 0")
    if args.output is None and not args.info:
        fail("请用 -o/--output 指定输出，或使用 --info 只查看媒体信息")

    if args.info:
        print_media_info(args.input)
        if args.output is None:
            return 0
        print()

    assert args.output is not None
    start_seconds = parse_time(args.start, "--start") if args.start else None
    duration_seconds = parse_time(args.duration, "--duration") if args.duration else None
    if duration_seconds is not None and duration_seconds <= 0:
        fail("--duration 必须大于 0")

    info = run_ffprobe(args.input)
    if info is not None and not get_video_stream(info):
        fail("输入文件中没有视频流")

    plan = build_output_plan(args.output, args.format)
    check_output_conflicts(plan, input_path, args.overwrite)

    total_duration = get_duration(info)
    fps = parse_rate(
        (get_video_stream(info) or {}).get("avg_frame_rate")
        or (get_video_stream(info) or {}).get("r_frame_rate")
    )
    frame_count = get_frame_count(info)

    if total_duration is not None and start_seconds is not None:
        if start_seconds >= total_duration:
            fail(
                f"--start={args.start} 超出视频时长 {format_duration(total_duration)}"
            )

    remaining_seconds: float | None = None
    if total_duration is not None:
        remaining_seconds = total_duration - (start_seconds or 0.0)
        if duration_seconds is not None:
            remaining_seconds = min(remaining_seconds, duration_seconds)

    expected_frames: int | None = None
    if remaining_seconds is not None:
        output_fps = args.fps or fps
        if output_fps:
            expected_frames = max(0, int(round(remaining_seconds * output_fps)))
    elif (
        frame_count is not None
        and start_seconds is None
        and duration_seconds is None
        and args.fps is None
    ):
        expected_frames = frame_count

    command = build_command(args, plan, start_seconds, duration_seconds)

    source_video = get_video_stream(info) or {}
    source_fps_text = f"{fps:.2f} fps" if fps else "帧率未知"
    print(f"输入: {args.input}")
    if source_video:
        print(
            f"源视频: {source_video.get('codec_name', '?')} "
            f"{source_video.get('width', '?')}x{source_video.get('height', '?')}, "
            f"{source_fps_text}, {format_duration(total_duration)}"
        )
    if plan.kind == "raw":
        print(f"输出: RAW {args.pix_fmt} -> {plan.target}")
    else:
        assert plan.pattern is not None
        print(f"输出: 图片序列 -> {plan.pattern}")

    if args.dry_run:
        print("\n将执行:")
        print(render_command(command))
        return 0

    try:
        plan.target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        fail(f"无法创建输出目录 {plan.target.parent}: {exc}")
    elapsed, decoded_frames = run_ffmpeg(
        command,
        plan,
        expected_frames,
        remaining_seconds,
        args.verbose,
    )
    print_decode_summary(plan, info, elapsed, args.pix_fmt, decoded_frames)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
