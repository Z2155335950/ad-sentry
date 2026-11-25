"""公众人物：响应归一化与 P1 定级，以及「不落人脸数据」的约束。"""

import json

from app.config import settings
from app.tools import base, celebrity
from app.tools.signals import celebrity_signals

FRAME = {"index": 1, "scene_index": 1, "timestamp": 3.5}


def test_normalize_response_builds_bbox_and_confidence():
    payload = {
        "CelebrityFaces": [
            {
                "Name": "Demo Celebrity",
                "MatchConfidence": 97.5,
                "BoundingBox": {"Left": 0.2, "Top": 0.1, "Width": 0.4, "Height": 0.5},
            }
        ],
        "UnrecognizedFaces": [{"BoundingBox": {}}],
    }
    normalized = celebrity.normalize_response(payload)
    face = normalized["celebrities"][0]
    assert face["name"] == "Demo Celebrity"
    assert face["confidence"] == 0.975
    assert face["bbox"] == "0.200,0.100,0.600,0.600"
    assert normalized["unrecognized_faces"] == 1


def test_confident_hit_is_p1_with_reference_and_bbox():
    result = base.ToolResult(
        tool="celebrity",
        status="ok",
        mode="mock",
        data={
            "provider": "aws_rekognition(mock)",
            "celebrities": [
                {"name": "Demo Celebrity", "confidence": 0.97, "bbox": "0.2,0.1,0.6,0.6"}
            ],
        },
    )
    signals = celebrity_signals(result, frame=FRAME)
    assert len(signals) == 1
    signal = signals[0]
    assert signal["level"] == "P1"
    assert signal["dedupe_key"] == "celebrity:Demo Celebrity"
    kinds = {ev["kind"] for ev in signal["evidence"]}
    assert {"reference", "bbox", "timestamp"} <= kinds


def test_low_confidence_hit_is_dropped():
    result = base.ToolResult(
        tool="celebrity",
        status="ok",
        mode="mock",
        data={"celebrities": [{"name": "Someone", "confidence": 0.3, "bbox": "0,0,1,1"}]},
    )
    assert celebrity_signals(result, frame=FRAME) == []
    assert settings.celebrity_min_confidence > 0.3


def test_tool_data_holds_no_image_payload():
    result = base.ToolResult(
        tool="celebrity",
        status="ok",
        mode="mock",
        data={
            "provider": "aws_rekognition(mock)",
            "celebrities": [{"name": "Demo", "confidence": 0.9, "bbox": "0,0,1,1"}],
            "unrecognized_faces": 0,
        },
    )
    serialized = json.dumps(result.data)
    assert "base64" not in serialized
    assert "image" not in serialized.lower()


def test_crop_face_bytes_stays_in_memory(tmp_path):
    from PIL import Image

    source = tmp_path / "frame.jpg"
    Image.new("RGB", (200, 200), (120, 90, 70)).save(source)
    data = celebrity.crop_face_bytes(source, "0.25,0.25,0.75,0.75")
    assert data[:2] == b"\xff\xd8", "裁剪结果应为 JPEG 字节"
    assert [path.name for path in tmp_path.iterdir()] == ["frame.jpg"], "不得落盘人脸图"
