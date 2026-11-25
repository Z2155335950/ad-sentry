"""生成 Phase 2 验收素材与 mock fixture。

产物：
- `fixtures/media/risk_ad_10s.mp4`：10s、3 分镜，第 1 镜为合成「图库照片」，
  第 2 镜为合成人像，第 3 镜为促销文字；音轨为合成旋律。
- `fixtures/cache/image_search.json` / `celebrity.json` / `acrcloud.json`：
  按素材 sha256 + 分镜/窗口序号键控的 mock 响应，对应三个「预期信号」。

mock 只用于演示与验收；真实凭证齐全后把 TOOLS_MODE 切到 auto 即可接真。
运行：python scripts/make_p2_fixtures.py [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
VIDEO = ROOT / "fixtures" / "media" / "risk_ad_10s.mp4"
CACHE = ROOT / "fixtures" / "cache"

WIDTH, HEIGHT, FPS, DURATION = 960, 540, 25, 10.0
SCENES = (
    (0.0, 3.5, (18, 32, 58), (70, 120, 190)),
    (3.5, 7.0, (52, 24, 48), (150, 70, 120)),
    (7.0, 10.0, (110, 40, 20), (235, 150, 60)),
)
FONT_CANDIDATES = (
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/arialbd.ttf",
)


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size=size)


def gradient(top, bottom) -> np.ndarray:
    rows = np.linspace(0.0, 1.0, HEIGHT, dtype=np.float32)[:, None]
    top_arr = np.array(top, dtype=np.float32)[None, :]
    bottom_arr = np.array(bottom, dtype=np.float32)[None, :]
    data = top_arr + (bottom_arr - top_arr) * rows
    return np.repeat(data[:, None, :], WIDTH, axis=1).astype(np.uint8)


def centered(draw: ImageDraw.ImageDraw, text: str, text_font, y: int, fill) -> None:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=text_font)
    draw.text(((WIDTH - (right - left)) // 2 - left, y - top), text, font=text_font, fill=fill)


def draw_stock_photo(draw: ImageDraw.ImageDraw) -> None:
    draw.rounded_rectangle((90, 110, 500, 430), radius=24, fill=(235, 240, 250))
    draw.ellipse((140, 170, 300, 330), fill=(64, 130, 200))
    draw.rectangle((320, 190, 450, 330), fill=(240, 180, 70))
    draw.polygon([(140, 400), (250, 300), (360, 400)], fill=(70, 160, 110))
    draw.text((120, 446), "STOCK / PREMIUM", font=font(30), fill=(250, 250, 250))
    centered(draw, "PREMIUM PHOTO", font(64), 40, (255, 255, 255))


def draw_portrait(draw: ImageDraw.ImageDraw) -> None:
    draw.rounded_rectangle((330, 110, 630, 470), radius=28, fill=(250, 236, 226))
    draw.ellipse((390, 150, 570, 390), fill=(244, 205, 175))
    draw.ellipse((372, 120, 588, 260), fill=(48, 36, 34))
    draw.ellipse((420, 225, 446, 245), fill=(255, 255, 255))
    draw.ellipse((514, 225, 540, 245), fill=(255, 255, 255))
    draw.ellipse((426, 229, 440, 241), fill=(30, 30, 30))
    draw.ellipse((520, 229, 534, 241), fill=(30, 30, 30))
    draw.line((470, 250, 460, 300), fill=(190, 140, 115), width=4)
    draw.arc((450, 300, 510, 345), start=10, end=170, fill=(150, 60, 60), width=6)
    draw.text((330, 420), "DEMO PERSON", font=font(30), fill=(90, 60, 60))
    centered(draw, "MEET OUR STAR", font(52), 40, (255, 255, 255))


def draw_sale(draw: ImageDraw.ImageDraw) -> None:
    centered(draw, "SALE 50% OFF", font(90), 190, (255, 255, 255))
    centered(draw, "SHOP NOW", font(54), 320, (255, 226, 140))


def render_frame(t: float, backgrounds: dict[int, np.ndarray]) -> np.ndarray:
    index = next(i for i, scene in enumerate(SCENES) if scene[0] <= t < scene[1] + 1e-6)
    image = Image.fromarray(backgrounds[index])
    draw = ImageDraw.Draw(image)
    if index == 0:
        draw_stock_photo(draw)
    elif index == 1:
        draw_portrait(draw)
    else:
        draw_sale(draw)
    progress = int(WIDTH * min(1.0, t / DURATION))
    draw.rectangle((0, HEIGHT - 8, progress, HEIGHT), fill=(255, 255, 255))
    return np.asarray(image)[:, :, ::-1].copy()


def melody_wav(dst: Path, rate: int = 44100) -> None:
    time_axis = np.arange(int(DURATION * rate), dtype=np.float32) / rate
    notes = [261.63, 329.63, 392.00, 523.25, 392.00, 329.63, 293.66, 261.63]
    signal = np.zeros_like(time_axis)
    note_len = DURATION / len(notes)
    for index, frequency in enumerate(notes):
        start = index * note_len
        mask = (time_axis >= start) & (time_axis < start + note_len)
        local = time_axis[mask] - start
        envelope = np.minimum(1.0, local / 0.05) * np.minimum(
            1.0, (note_len - local) / 0.1
        )
        tone = 0.5 * np.sin(2 * np.pi * frequency * local)
        tone += 0.25 * np.sin(2 * np.pi * frequency * 2 * local)
        signal[mask] = 0.22 * envelope * tone
    samples = (np.clip(signal, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(str(dst), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(samples.tobytes())


def build_video() -> None:
    import cv2
    from app.pipeline import ffmpeg

    backgrounds = {i: gradient(scene[2], scene[3]) for i, scene in enumerate(SCENES)}
    with tempfile.TemporaryDirectory(prefix="adsentry_p2_") as tmp:
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

        audio = tmp_dir / "melody.wav"
        melody_wav(audio)

        attempts = (
            ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p"],
            ["-c:v", "mpeg4", "-q:v", "4"],
        )
        for codec in attempts:
            result = ffmpeg.run_raw(
                [
                    "-y",
                    "-i",
                    str(silent),
                    "-i",
                    str(audio),
                    *codec,
                    "-c:a",
                    "aac",
                    "-b:a",
                    "96k",
                    "-t",
                    f"{DURATION}",
                    str(VIDEO),
                ]
            )
            if result.returncode == 0 and VIDEO.exists():
                break
        else:
            raise RuntimeError("视频编码失败")


def write_mocks(sha256: str) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    image_search = {
        "schema": 1,
        "note": "Phase 2 mock：按素材 sha256 + 分镜索引键控，由 make_p2_fixtures.py 生成",
        "assets": {
            sha256: {
                "scenes": {
                    "0": {
                        "provider": "google_vision(mock)",
                        "matches": [
                            {
                                "url": "https://www.shutterstock.com/image-photo/demo-123456",
                                "title": "Premium Stock Photo - Demo",
                                "match_type": "full",
                                "domain_kind": "stock",
                            },
                            {
                                "url": "https://example-blog.test/design/inspiration",
                                "title": "Design inspiration roundup",
                                "match_type": "visually_similar",
                                "domain_kind": "blog",
                            },
                        ],
                        "best_guess_labels": ["stock photo"],
                    }
                }
            }
        },
    }
    celebrity = {
        "schema": 1,
        "note": "Phase 2 mock：合成人像，仅演示名人识别链路，不指向任何真实公众人物",
        "assets": {
            sha256: {
                "scenes": {
                    "1": {
                        "provider": "aws_rekognition(mock)",
                        "celebrities": [
                            {
                                "name": "Demo Celebrity",
                                "confidence": 0.97,
                                "bbox": "0.390,0.150,0.570,0.390",
                            }
                        ],
                        "unrecognized_faces": 0,
                    }
                }
            }
        },
    }
    acrcloud = {
        "schema": 1,
        "note": "Phase 2 mock：合成旋律，窗口 1 命中示例曲目，其余窗口无匹配",
        "assets": {
            sha256: {
                "windows": {
                    "0": {
                        "provider": "acrcloud(mock)",
                        "status": "identified",
                        "title": "Demo Summer Track",
                        "artists": ["Demo Artist"],
                        "album": "Demo Album",
                        "label": "Demo Records",
                        "score": 0.93,
                        "play_offset_ms": 1200,
                        "duration_ms": 180000,
                        "external_ids": {"isrc": "DEMO00000001"},
                    },
                    "1": {"provider": "acrcloud(mock)", "status": "no_match", "code": 1001},
                    "2": {"provider": "acrcloud(mock)", "status": "no_match", "code": 1001},
                }
            }
        },
    }
    for name, payload in (
        ("image_search", image_search),
        ("celebrity", celebrity),
        ("music", acrcloud),
    ):
        path = CACHE / f"{name}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"written {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="重建视频与 mock")
    args = parser.parse_args()

    if not VIDEO.exists() or args.force:
        print("生成风险演示视频（10s，合成画面 + 合成旋律）...")
        build_video()

    from app.pipeline import ffmpeg

    info = ffmpeg.probe(VIDEO)
    if not info.get("has_audio"):
        raise SystemExit("生成的视频缺少音轨")
    sha256 = hashlib.sha256(VIDEO.read_bytes()).hexdigest()
    write_mocks(sha256)
    print(f"asset sha256: {sha256}")
    print(
        f"  {VIDEO.name}: {info['duration']}s, "
        f"{info['width']}x{info['height']}, audio={info['has_audio']}"
    )
    print("预期信号：copyright_image P0 / celebrity P1 / bgm P2")


if __name__ == "__main__":
    main()
