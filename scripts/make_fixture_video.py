"""生成 Phase 1 验收视频：15 秒、3 个分镜、含英/中画面文字与英文口播。

- 画面用 Pillow 合成，切点固定在 5s / 10s，供 PySceneDetect 检出；
- 口播用 Windows SAPI 合成（Microsoft Zira），没有则用默认语音，再没有就失败退出；
- 编码用 imageio-ffmpeg 自带二进制，无需系统安装 ffmpeg。

只需运行一次：python scripts/make_fixture_video.py [--force]
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures" / "media" / "sample_15s.mp4"

WIDTH, HEIGHT, FPS, DURATION = 1280, 720, 25, 15.0
SCENES = (
    (0.0, 5.0, "SALE 50% OFF", "LOGO", (16, 24, 46), (44, 88, 150)),
    (5.0, 10.0, "NEW ARRIVAL", "NIKE", (10, 74, 62), (60, 190, 140)),
    (10.0, 15.0, "限时优惠", "立即购买", (120, 52, 12), (240, 150, 40)),
)
SPEECH = (
    "This is a fifteen second test advertisement. Big sale today. "
    "Shop now for the new arrival. Limited time offer."
)

FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/arialbd.ttf",
)

# 优先 H.264；部分 ffmpeg 构建不带 libx264，退回 mpeg4
VIDEO_CODECS = (
    ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"],
    ["-c:v", "mpeg4", "-q:v", "4"],
)


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)


def gradient(top: tuple[int, int, int], bottom: tuple[int, int, int]) -> np.ndarray:
    rows = np.linspace(0.0, 1.0, HEIGHT, dtype=np.float32)[:, None]
    top_arr = np.array(top, dtype=np.float32)[None, :]
    bottom_arr = np.array(bottom, dtype=np.float32)[None, :]
    data = top_arr + (bottom_arr - top_arr) * rows
    return np.repeat(data[:, None, :], WIDTH, axis=1).astype(np.uint8)


def draw_centered(draw: ImageDraw.ImageDraw, text: str, text_font, y: int, fill) -> None:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=text_font)
    draw.text(((WIDTH - (right - left)) // 2 - left, y - top), text, font=text_font, fill=fill)


def render_frame(t: float, backgrounds: dict[int, np.ndarray]) -> np.ndarray:
    scene_index = next(i for i, scene in enumerate(SCENES) if scene[0] <= t < scene[1] + 1e-6)
    image = Image.fromarray(backgrounds[scene_index])
    draw = ImageDraw.Draw(image)
    _, _, headline, subline, _, _ = SCENES[scene_index]

    draw_centered(draw, headline, font(120), 210, (255, 255, 255))
    draw_centered(draw, subline, font(64), 420, (255, 214, 102))

    if scene_index == 0:
        # 画面右上角的假 logo 块，后续 logo 检测的 bbox 参照
        draw.rounded_rectangle(
            (WIDTH - 300, 60, WIDTH - 90, 170), radius=20, fill=(205, 38, 38)
        )
        draw.text((WIDTH - 275, 92), "LOGO", font=font(56), fill=(255, 255, 255))

    progress = int(WIDTH * min(1.0, t / DURATION))
    draw.rectangle((0, HEIGHT - 10, progress, HEIGHT), fill=(255, 255, 255))
    return np.asarray(image)[:, :, ::-1].copy()  # RGB → BGR


def speech_wav(dst: Path) -> None:
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "try { $s.SelectVoice('Microsoft Zira Desktop') } catch {}; "
        "$s.Rate = 0; "
        f"$s.SetOutputToWaveFile('{dst.as_posix()}'); "
        f"$s.Speak('{SPEECH}'); "
        "$s.Dispose();"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0 or not dst.exists() or dst.stat().st_size == 0:
        raise RuntimeError(f"SAPI 语音合成失败: {(result.stderr or '').strip()[:300]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="覆盖已有文件")
    args = parser.parse_args()

    if OUT.exists() and not args.force:
        print(f"已存在：{OUT}（--force 覆盖）")
        return

    import cv2
    from app.pipeline import ffmpeg

    OUT.parent.mkdir(parents=True, exist_ok=True)
    backgrounds = {
        i: gradient(scene[4], scene[5]) for i, scene in enumerate(SCENES)
    }

    with tempfile.TemporaryDirectory(prefix="adsentry_fixture_") as tmp:
        tmp_dir = Path(tmp)
        silent = tmp_dir / "silent.mp4"
        writer = cv2.VideoWriter(
            str(silent), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (WIDTH, HEIGHT)
        )
        if not writer.isOpened():
            raise RuntimeError("OpenCV 无法创建视频写入器")
        for frame_index in range(int(FPS * DURATION)):
            writer.write(render_frame(frame_index / FPS, backgrounds))
        writer.release()

        speech = tmp_dir / "speech.wav"
        speech_wav(speech)
        padded = tmp_dir / "speech_padded.wav"
        pad = ffmpeg.run_raw(
            [
                "-y", "-i", str(speech), "-af", "apad", "-t", f"{DURATION}",
                "-ar", "44100", "-ac", "1", str(padded),
            ]
        )
        if pad.returncode != 0:
            raise RuntimeError(f"音频补静音失败: {pad.stderr[-300:]}")

        for video_codec in VIDEO_CODECS:
            mux = ffmpeg.run_raw(
                [
                    "-y",
                    "-i",
                    str(silent),
                    "-i",
                    str(padded),
                    *video_codec,
                    "-c:a",
                    "aac",
                    "-b:a",
                    "96k",
                    "-t",
                    f"{DURATION}",
                    str(OUT),
                ]
            )
            if mux.returncode == 0 and OUT.exists():
                break
            print(f"编码失败（{video_codec[1]}），尝试降级: {mux.stderr[-200:]}")
        else:
            raise RuntimeError("视频编码失败")

    info = ffmpeg.probe(OUT)
    print(f"written {OUT}")
    print(
        f"  duration={info['duration']}s size={info['width']}x{info['height']}"
        f" audio={info['has_audio']}"
    )
    if not info["has_audio"] or not info["duration"]:
        sys.exit("生成的视频缺少音轨或时长，检查 ffmpeg 编码输出")


if __name__ == "__main__":
    main()
