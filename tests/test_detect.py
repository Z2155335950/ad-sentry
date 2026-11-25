"""工具编排与融合：帧选择、去重冲突处理。"""

from app.pipeline.detect import select_frames
from app.pipeline.fuse import dedupe_signals


def test_select_frames_round_robin_by_scene():
    frames = [
        {"index": 0, "scene_index": 0, "sharpness": 10.0, "timestamp": 1.0},
        {"index": 1, "scene_index": 0, "sharpness": 9.0, "timestamp": 2.0},
        {"index": 2, "scene_index": 1, "sharpness": 8.0, "timestamp": 5.0},
        {"index": 3, "scene_index": 2, "sharpness": 7.0, "timestamp": 9.0},
    ]
    selected = select_frames(frames, 3)
    assert [frame["scene_index"] for frame in selected] == [0, 1, 2]
    assert [frame["timestamp"] for frame in selected] == [1.0, 5.0, 9.0]


def test_select_frames_limits_and_handles_empty():
    assert select_frames([], 5) == []
    frames = [{"index": i, "scene_index": 0, "sharpness": i, "timestamp": i} for i in range(5)]
    assert len(select_frames(frames, 2)) == 2


def test_dedupe_keeps_highest_level_for_same_key():
    signals = [
        {"category": "bgm", "level": "P2", "dedupe_key": "bgm:track", "evidence": []},
        {"category": "bgm", "level": "P1", "dedupe_key": "bgm:track", "evidence": []},
    ]
    deduped = dedupe_signals(signals)
    assert len(deduped) == 1
    assert deduped[0]["level"] == "P1"


def test_dedupe_keeps_distinct_keys():
    signals = [
        {"category": "copyright_image", "level": "P0", "dedupe_key": "a", "evidence": []},
        {"category": "copyright_image", "level": "P0", "dedupe_key": "b", "evidence": []},
    ]
    assert len(dedupe_signals(signals)) == 2
