# FFmpeg 视频编码 / 解码工具使用说明

本目录包含两个独立的 Python 脚本，直接调用系统中的 `ffmpeg` / `ffprobe`：

| 脚本 | 作用 | 典型输入 | 典型输出 |
| --- | --- | --- | --- |
| [encode_video.py](encode_video.py) | 视频编码 | 视频、图片序列、RAW YUV/RGB | H.264 / H.265 视频 |
| [decode_video.py](decode_video.py) | 视频解码 | H.264 / H.265 等视频 | PNG/JPEG 图片序列、RAW YUV |

两个脚本都只依赖 Python 标准库，不需要安装 `ffmpeg-python`、OpenCV 等第三方包。

---

## 1. 环境要求

- Python 3.10 或更高版本
- FFmpeg 5.1 或更高版本（解码器使用了 `-fps_mode`）
- `ffmpeg` 和 `ffprobe` 已加入系统 `PATH`

检查环境：

```powershell
python --version
ffmpeg -version
ffprobe -version
```

### Windows 安装 FFmpeg

任选一种方式：

```powershell
# winget
winget install --id Gyan.FFmpeg -e

# 或 Scoop
scoop install ffmpeg

# 或 Chocolatey
choco install ffmpeg
```

也可以从 <https://www.gyan.dev/ffmpeg/builds/> 下载完整版，解压后把 `bin` 目录加入 `PATH`。

### macOS / Linux

```bash
# macOS
brew install ffmpeg

# Ubuntu / Debian
sudo apt update
sudo apt install ffmpeg
```

### ffmpeg 不在 PATH 时

可以临时指定可执行文件路径（PowerShell）：

```powershell
$env:FFMPEG_BIN  = "D:\ffmpeg\bin\ffmpeg.exe"
$env:FFPROBE_BIN = "D:\ffmpeg\bin\ffprobe.exe"
```

永久写入当前用户环境变量：

```powershell
[Environment]::SetEnvironmentVariable("FFMPEG_BIN", "D:\ffmpeg\bin\ffmpeg.exe", "User")
[Environment]::SetEnvironmentVariable("FFPROBE_BIN", "D:\ffmpeg\bin\ffprobe.exe", "User")
```

重新打开终端后生效。

> 仅有 `ffmpeg` 时仍然可以编码/解码，但无法显示媒体信息，进度估算也会缺失。`--info` 必须依赖 `ffprobe`。

---

## 2. 一分钟上手

```powershell
# 1) 编码：MP4 转 H.264 + AAC
python encode_video.py input.mp4 output.mp4

# 2) 解码：导出为 PNG 图片序列
python decode_video.py output.mp4 -o frames/

# 3) 查看输出信息
python decode_video.py output.mp4 --info
```

导出后得到：

```text
frames/
├── frame_000000.png
├── frame_000001.png
├── frame_000002.png
└── ...
```

帧编号从 `000000` 开始，默认 6 位数字。

---

## 3. 编码器：encode_video.py

### 3.1 基本语法

```powershell
python encode_video.py <输入> <输出> [选项]
```

输出支持：`.mp4`、`.mov`、`.m4v`、`.mkv`。推荐 `.mp4`。

### 3.2 支持的输入类型

| 输入类型 | 示例 | 说明 |
| --- | --- | --- |
| 普通视频 | `input.mp4` | 自动解码后重新编码，不做流复制 |
| 图片序列 | `frames/frame_%06d.png` | 文件名必须含 `%d` 或 `%06d` |
| RAW 像素 | `input.yuv` | 必须提供宽、高、像素格式和帧率 |

脚本默认根据文件名自动判断：

- 文件名含 `%`：按图片序列处理
- 扩展名为 `.yuv`、`.raw`、`.rgb`、`.rgba`、`.gray`、`.nv12`：按 RAW 处理
- 其他情况：按普通视频处理

也可以用 `--input-type` 强制指定：

```powershell
--input-type video
--input-type images
--input-type rawvideo
```

### 3.3 完整参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `input` | 必填 | 输入视频、图片序列模式或 RAW 文件 |
| `output` | 必填 | 输出文件，扩展名必须是 `.mp4/.mov/.m4v/.mkv` |
| `--input-type` | `auto` | `auto/video/images/rawvideo` |
| `--codec` | `libx264` | 视频编码器：`libx264`（H.264）或 `libx265`（H.265） |
| `--crf` | `23` | 质量因子 0–51，越小质量越高、文件越大 |
| `--preset` | `medium` | 编码速度预设，越慢压缩率越高 |
| `--pix-fmt` | `yuv420p` | 输出像素格式，兼容性最好 |
| `--audio-bitrate` | `192k` | AAC 音频码率 |
| `--no-audio` | 关闭 | 输出不包含音频 |
| `--fps` | 图片/RAW 为 30 | 仅用于图片序列和 RAW 输入 |
| `--width` | 无 | RAW 输入宽度，必填 |
| `--height` | 无 | RAW 输入高度，必填 |
| `--raw-pix-fmt` | `yuv420p` | RAW 输入的像素格式 |
| `--overwrite` | 关闭 | 允许覆盖已存在的输出 |
| `--dry-run` | 关闭 | 只打印 ffmpeg 命令，不执行 |
| `--verbose` | 关闭 | 显示完整 ffmpeg 日志 |

`--preset` 可选值：

```text
ultrafast, superfast, veryfast, faster, fast,
medium, slow, slower, veryslow, placebo
```

### 3.4 常用示例

#### 视频压缩 / 转码

```powershell
# 默认 H.264，CRF 23
python encode_video.py input.mov compressed.mp4

# 更高质量
python encode_video.py input.mkv high_quality.mp4 --crf 18 --preset slow

# 更小体积
python encode_video.py input.mp4 small.mp4 --crf 28 --preset slower

# 降低音频码率
python encode_video.py input.mp4 output.mp4 --crf 26 --audio-bitrate 128k

# 不要音频
python encode_video.py input.mp4 silent.mp4 --no-audio
```

#### 图片序列编码为视频

```powershell
# 30 fps
python encode_video.py "frames/frame_%06d.png" output.mp4 --fps 30

# 24 fps，高质量
python encode_video.py "render/frame_%06d.png" render.mp4 --fps 24 --crf 18

# JPEG 序列
python encode_video.py "frames/frame_%06d.jpg" output.mp4 --fps 25
```

图片序列的 `--fps` 决定最终播放速度。例如 300 张图按 30 fps 编码，视频长度为 10 秒。

#### RAW YUV 编码为视频

```powershell
python encode_video.py input.yuv output.mp4 --input-type rawvideo --width 1920 --height 1080 --raw-pix-fmt yuv420p --fps 30
```

`--raw-pix-fmt` 必须与 RAW 文件实际格式一致，否则画面会花屏或颜色异常。

#### H.265 / HEVC 编码

```powershell
python encode_video.py input.mp4 output_hevc.mp4 --codec libx265 --crf 28 --preset slow
```

MP4/MOV/M4V 容器下会自动写入 `hvc1` 标签，提升 Apple 设备兼容性。H.265 编码更慢，老设备兼容性不如 H.264。

#### 先检查命令

```powershell
python encode_video.py input.mp4 output.mp4 --crf 20 --preset slow --dry-run
```

只打印最终 ffmpeg 命令，不实际执行，适合确认参数。

---

## 4. 解码器：decode_video.py

### 4.1 基本语法

```powershell
python decode_video.py <输入视频> -o <输出> [选项]
```

只查看信息时可以省略 `-o`：

```powershell
python decode_video.py input.mp4 --info
```

### 4.2 输出路径规则

| `-o` 写法 | 输出类型 | 实际结果 |
| --- | --- | --- |
| `frames/` | 图片序列 | `frames/frame_%06d.png` |
| `frames` | 图片序列 | 同上 |
| `frames/shot_%06d.png` | 图片序列 | 按指定模式命名 |
| `frames/shot.png` | 图片序列 | 自动变为 `frames/shot_%06d.png` |
| `output.yuv` | RAW | 默认 yuv420p |
| `output.raw` | RAW | 默认 yuv420p |

支持的图片扩展名：`.png`、`.jpg`、`.jpeg`、`.bmp`、`.webp`、`.tif`、`.tiff`。

RAW 扩展名：`.yuv`、`.raw`、`.rgb`、`.rgba`、`.gray`、`.nv12`。

> 扩展名只用于判断“这是 RAW 输出”，不会自动设置像素格式。默认仍是 `yuv420p`；输出 `.rgb`、`.rgba`、`.gray`、`.nv12` 时请用 `--pix-fmt` 显式指定对应格式。

也可以用 `--format` 显式指定：

```powershell
--format images
--format raw
```

### 4.3 完整参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `input` | 必填 | 输入视频文件 |
| `-o, --output` | 无 | 输出目录、图片模式或 RAW 文件 |
| `--format` | `auto` | `auto/images/raw` |
| `--pix-fmt` | `yuv420p` | RAW 输出的像素格式 |
| `--fps` | 原帧率 | 按指定帧率抽帧，例如 `1` 表示每秒 1 帧 |
| `--start` | 0 | 起始时间，支持秒数或 `HH:MM:SS` |
| `--duration` | 全部 | 解码时长，支持秒数或 `HH:MM:SS` |
| `--overwrite` | 关闭 | 允许覆盖已有输出 |
| `--info` | 关闭 | 打印媒体信息，可单独使用 |
| `--dry-run` | 关闭 | 只打印 ffmpeg 命令，不执行 |
| `--verbose` | 关闭 | 显示完整 ffmpeg 日志 |

### 4.4 常用示例

#### 查看媒体信息

```powershell
python decode_video.py input.mp4 --info
```

输出包括容器、时长、码率、视频编码、分辨率、像素格式、帧率、音频编码、采样率、声道等。

#### 导出全部帧

```powershell
python decode_video.py input.mp4 -o frames/
```

#### 导出 JPEG

```powershell
python decode_video.py input.mp4 -o "frames/frame_%06d.jpg"
```

JPEG 是有损格式；需要逐帧无损处理时使用 PNG。

#### 每秒抽 1 帧

```powershell
python decode_video.py input.mp4 -o thumbs/ --fps 1
```

适合生成预览图、缩略图或做画面分析。

#### 只解码一段

```powershell
python decode_video.py input.mp4 -o "clip/frame_%06d.png" --start 00:01:30 --duration 10
```

表示从第 1 分 30 秒开始，解码 10 秒。

也支持秒数：

```powershell
python decode_video.py input.mp4 -o clip/ --start 12.5 --duration 3.2
```

#### 解码为 RAW YUV

```powershell
python decode_video.py input.mp4 -o decoded.yuv --format raw --pix-fmt yuv420p
```

RAW 文件没有文件头，不包含宽高、帧率、像素格式和音频信息。读取时必须由程序自己知道这些参数。

YUV420p 单帧字节数：

```text
width × height × 3 / 2
```

例如 1920×1080：

```text
1920 × 1080 × 1.5 = 3,110,400 字节/帧
```

#### 解码为 RGB24

```powershell
python decode_video.py input.mp4 -o decoded.rgb --format raw --pix-fmt rgb24
```

RGB24 单帧字节数：

```text
width × height × 3
```

#### 覆盖已有输出

```powershell
python decode_video.py input.mp4 -o frames/ --overwrite
```

如果目录中已经有匹配的帧文件，脚本默认拒绝执行；`--overwrite` 会覆盖本次生成的文件，但不会删除目录中多出来的旧帧。

#### 只打印命令

```powershell
python decode_video.py input.mp4 -o frames/ --fps 1 --dry-run
```

---

## 5. 典型工作流

### 5.1 视频压缩后发布

```powershell
python encode_video.py raw.mov web.mp4 --codec libx264 --crf 24 --preset slow --audio-bitrate 128k
```

MP4 输出会自动启用 `faststart`，有利于网络播放时尽快起播。

### 5.2 视频转图片序列再重新编码

```powershell
# 解码
python decode_video.py input.mp4 -o frames/

# 处理 frames/ 中的图片……

# 重新编码
python encode_video.py "frames/frame_%06d.png" restored.mp4 --fps 30 --crf 20
```

必须保证 `--fps` 与原视频一致，否则播放速度会变化。

### 5.3 RAW YUV 往返

```powershell
# 视频 -> YUV420p
python decode_video.py input.mp4 -o decoded.yuv --format raw --pix-fmt yuv420p

# YUV420p -> 视频
python encode_video.py decoded.yuv restored.mp4 --input-type rawvideo --width 1920 --height 1080 --raw-pix-fmt yuv420p --fps 30
```

这里的宽、高、像素格式、帧率必须与解码时一致。

### 5.4 生成 H.265 压缩版本

```powershell
python encode_video.py input.mp4 output_hevc.mp4 --codec libx265 --crf 28 --preset slow --audio-bitrate 128k
```

### 5.5 生成预览缩略图

```powershell
python decode_video.py input.mp4 -o preview/ --fps 0.2
```

`0.2` 表示平均每 5 秒 1 帧。

---

## 6. 参数与质量建议

### 6.1 CRF

| CRF | 画质 / 体积 | 适用场景 |
| --- | --- | --- |
| 0 | 编码本身无损（像素格式转换除外），体积极大 | 中间母版、测试 |
| 16–18 | 接近视觉无损，体积大 | 高质量存档 |
| 20–23 | 质量与体积平衡 | 默认、常规发布 |
| 24–28 | 体积明显减小 | 网络传输、移动端 |
| 30+ | 体积小，画质下降明显 | 预览、低码率场景 |

CRF 不是固定码率。相同 CRF 下，运动越多、细节越多的视频，文件通常越大。

### 6.2 preset

- `ultrafast` / `veryfast`：编码快，压缩率低
- `medium`：默认，速度与压缩率平衡
- `slow` / `slower`：更慢，但同画质下体积通常更小
- `placebo`：极慢，收益很小，一般不建议

### 6.3 编码器选择

| 编码器 | 优点 | 缺点 |
| --- | --- | --- |
| `libx264` | 兼容性最好，编码快 | 同画质下体积大于 H.265 |
| `libx265` | 同画质下体积更小 | 编码慢，老设备/浏览器兼容性较差 |

需要最大兼容性时选 H.264；需要更小体积且能接受较慢编码时选 H.265。

### 6.4 像素格式

编码器的 `--pix-fmt`：

- `yuv420p`：默认，兼容性最好，要求宽高通常为偶数
- `yuv444p`：保留更多色度信息，但播放器兼容性较差

`libx264` / `libx265` 不能直接输出 `rgb24`。如果需要 RGB 数据，请用解码器的 `--pix-fmt rgb24` 导出 RAW，或在后续程序中转换。

### 6.5 音频码率

| 码率 | 适用场景 |
| --- | --- |
| `96k–128k` | 语音、低码率视频 |
| `192k` | 默认，常规立体声 |
| `256k` | 高质量音乐内容 |

---

## 7. 注意事项

1. **编码器会重新编码视频**，不做 `-c:v copy` 流复制。
2. 编码器只保留**第一条视频流**和**第一条音频流**；字幕和其他音轨不会写入输出。
3. 解码器只解码**第一条视频流**，不导出音频和字幕。
4. 图片序列和 RAW 输出天然不包含音频。
5. 图片序列输入必须用 `%d` / `%06d` 表示编号；建议统一使用 6 位补零。
6. `--fps` 只用于图片序列和 RAW 输入；普通视频带有自己的帧率，脚本会拒绝给视频输入指定 `--fps`。
7. `yuv420p` 要求宽高为偶数。源视频宽高为奇数时，建议先用 ffmpeg 缩放：

   ```powershell
   ffmpeg -i input.mp4 -vf "scale=trunc(iw/2)*2:trunc(ih/2)*2" -c:a copy even.mp4
   ```

8. `--overwrite` 只覆盖本次生成的文件；解码图片时目录中多余的旧帧不会被自动删除，脚本会给出提示。
9. 编码失败时，脚本会尝试删除未完成的输出文件；解码图片序列失败时，可能留下部分帧。
10. 输入文件和输出文件不能是同一个文件，脚本会阻止这种操作。
11. 当前脚本使用 CPU 软件编码（`libx264` / `libx265`），未启用 NVENC、QSV、VideoToolbox 等硬件编码。
12. Windows 下脚本会把标准输出切换为 UTF-8，避免 PowerShell 捕获中文时乱码。
13. RAW 文件的扩展名**不会**自动改变像素格式：`--raw-pix-fmt` / `--pix-fmt` 的默认值始终是 `yuv420p`。例如输出 `.rgb` 文件时必须显式写 `--pix-fmt rgb24`，否则文件内容仍是 YUV420p。

---

## 8. 常见问题

### 找不到 ffmpeg

```text
错误: 找不到 ffmpeg：ffmpeg。请安装并加入 PATH，或设置 FFMPEG_BIN。
```

解决方式：

1. 执行 `ffmpeg -version` 确认命令可用。
2. 如果安装了但命令不可用，把 ffmpeg 的 `bin` 目录加入 `PATH`。
3. 或设置 `FFMPEG_BIN` / `FFPROBE_BIN`，见第 1 节。

### 输出文件已存在

```text
错误: 输出文件已存在: output.mp4（使用 --overwrite 覆盖）
```

确认可以覆盖后加 `--overwrite`：

```powershell
python encode_video.py input.mp4 output.mp4 --overwrite
```

### 图片序列输入报错

```text
错误: 图片序列输入的文件名必须包含 %d 或 %06d
```

正确写法：

```powershell
python encode_video.py "frames/frame_%06d.png" output.mp4 --fps 30
```

PowerShell 中建议给带 `%` 的路径加引号。

### RAW 输入缺少宽高

```text
错误: RAW 输入必须同时指定 --width 和 --height
```

```powershell
python encode_video.py input.yuv output.mp4 --input-type rawvideo --width 1280 --height 720 --fps 30
```

### 画面花屏或颜色异常

RAW 输入时，`--raw-pix-fmt` 必须和文件真实格式一致，例如：

```powershell
--raw-pix-fmt yuv420p
--raw-pix-fmt nv12
--raw-pix-fmt rgb24
```

宽、高、像素格式任意一个不匹配都会导致花屏、错位或颜色异常。

### 宽高不是偶数

`yuv420p` 通常要求偶数宽高：

```text
width not divisible by 2
height not divisible by 2
```

先用 ffmpeg 处理为偶数尺寸，或改用兼容性较差的 `yuv444p`。推荐前者。

### 只导出了一帧

检查 `--fps` 和 `--duration` 的组合。例如 `--fps 0.2 --duration 1` 在 1 秒内可能只产生 1 帧。需要全部帧时不要指定 `--fps`。

### H.265 视频无法播放

部分浏览器、播放器和硬件不支持 HEVC。优先改用 H.264：

```powershell
python encode_video.py input.mp4 output.mp4 --codec libx264
```

如果 ffmpeg 提示 `Unknown encoder 'libx265'`，说明当前 ffmpeg 构建未包含 libx265，请换用完整构建。

### 提示 `Option fps_mode not found`

FFmpeg 版本过旧。解码器使用了 `-fps_mode`，建议升级到 FFmpeg 5.1 或更高版本。

### 没有进度或无法使用 --info

通常是 `ffprobe` 不在 `PATH`。可以单独设置：

```powershell
$env:FFPROBE_BIN = "D:\ffmpeg\bin\ffprobe.exe"
```

---

## 9. 退出码

| 退出码 | 含义 |
| --- | --- |
| `0` | 成功 |
| `2` | 参数或路径校验失败 |
| 其他非零值 | ffmpeg 执行失败，通常与输入文件、编码器或输出格式有关 |
| `130` | 用户按下 Ctrl+C 取消 |

---

## 10. 获取帮助

```powershell
python encode_video.py --help
python decode_video.py --help
```

两个脚本的模块文档字符串中也包含常用示例，可以直接查看源码开头部分。
