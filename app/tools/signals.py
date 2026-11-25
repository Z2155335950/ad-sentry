"""工具结果 → 风险信号（Finding 候选）。定级照 PROMPT §8/§11，措辞不出现「侵权」。"""

from __future__ import annotations

from app.config import settings
from app.tools import base, websearch


def _frame_evidence(frame: dict) -> dict:
    return {
        "kind": "timestamp",
        "t_start": frame.get("timestamp"),
        "note": f"关键帧 #{frame.get('index')}（分镜 {frame.get('scene_index')}）",
    }


def websearch_signals(result: base.ToolResult, *, frame: dict) -> list[dict]:
    if not result.usable or result.status == "empty":
        return []
    assessed = websearch.assess(result.data)
    level = assessed["level"]
    if not level:
        return []

    matches = assessed["matches"]
    best = matches[0]
    domain_labels = {
        "stock": "图库站点",
        "ecommerce": "电商站点",
        "social": "社交平台",
        "blog": "博客",
        "unknown": "来源未知",
    }
    evidence = [
        {
            "kind": "reference",
            "ref_url": match["url"],
            "ref_record_id": f"web:{match['match_type']}",
            "note": " · ".join(
                part
                for part in (
                    match.get("title"),
                    domain_labels.get(match.get("domain_kind"), "来源未知"),
                    f"{match['match_type']} match",
                )
                if part
            ),
        }
        for match in matches
    ]
    evidence.append(_frame_evidence(frame))

    if level == "P0":
        suggestion = (
            f"检测到疑似版权图像：检索到图库站点页面「{best.get('title') or best['url']}」，"
            "建议上线前核实素材授权来源。"
        )
    elif level == "P1":
        suggestion = "检测到疑似版权图像：检索到匹配页面，来源性质未知，建议核实授权后再投放。"
    else:
        suggestion = "画面与公开图像存在视觉相似，建议补充素材来源说明后再投放。"

    return [
        {
            "tool": "image_search",
            "category": "copyright_image",
            "level": level,
            "confidence": assessed["confidence"],
            "suggestion": suggestion,
            "dedupe_key": f"copyright_image:{best['url']}",
            "evidence": evidence,
        }
    ]


def celebrity_signals(result: base.ToolResult, *, frame: dict) -> list[dict]:
    if not result.usable or result.status == "empty":
        return []
    signals = []
    for celebrity in result.data.get("celebrities") or []:
        confidence = float(celebrity.get("confidence") or 0.0)
        if confidence < settings.celebrity_min_confidence:
            continue
        name = celebrity.get("name") or "未具名公众人物"
        evidence = [
            {
                "kind": "reference",
                "ref_record_id": f"rekognition:{name}",
                "note": f"AWS Rekognition 名人识别（{result.mode}），置信 {confidence:.2f}",
            }
        ]
        if celebrity.get("bbox"):
            evidence.append(
                {"kind": "bbox", "bbox": celebrity["bbox"], "note": "人脸区域"}
            )
        evidence.append(_frame_evidence(frame))
        signals.append(
            {
                "tool": "celebrity",
                "category": "celebrity",
                "level": "P1",
                "confidence": confidence,
                "suggestion": f"疑似出现公众人物「{name}」，需确认肖像授权与使用范围。",
                "dedupe_key": f"celebrity:{name}",
                "evidence": evidence,
            }
        )
    return signals


def music_signals(windows: list[dict], results: list[base.ToolResult]) -> list[dict]:
    identified: dict[str, dict] = {}
    no_match_windows: list[dict] = []

    for window, result in zip(windows, results, strict=False):
        if not result.usable or result.status == "empty":
            continue
        data = result.data or {}
        status = data.get("status")
        if status == "identified" and data.get("title"):
            key = f"{data['title']}|{','.join(data.get('artists') or [])}"
            identified.setdefault(key, {**data, "window": window})
        elif status == "no_match":
            no_match_windows.append(window)

    signals = []
    for entry in list(identified.values())[:2]:
        artist = "、".join(entry.get("artists") or []) or "未知艺术家"
        window = entry["window"]
        evidence = [
            {
                "kind": "reference",
                "ref_record_id": f"acrcloud:{entry['title']}",
                "note": " · ".join(
                    part
                    for part in (
                        f"ACRCloud 识别：{entry['title']} - {artist}",
                        f"厂牌 {entry.get('label')}" if entry.get("label") else None,
                        f"匹配度 {entry['score']:.2f}" if entry.get("score") is not None else None,
                    )
                    if part
                ),
            },
            {
                "kind": "timestamp",
                "t_start": window["start"],
                "t_end": window["end"],
                "note": f"音频窗口 {window['index'] + 1}",
            },
        ]
        signals.append(
            {
                "tool": "music",
                "category": "bgm",
                "level": "P2",
                "confidence": entry.get("score"),
                "suggestion": (
                    f"识别到背景音乐《{entry['title']}》（{artist}），"
                    "请核对 BGM 授权范围与投放地域。"
                ),
                "dedupe_key": f"bgm:{entry['title']}",
                "evidence": evidence,
            }
        )

    if not identified and no_match_windows:
        window = no_match_windows[0]
        signals.append(
            {
                "tool": "music",
                "category": "bgm",
                "level": "P2",
                "confidence": None,
                "suggestion": "未识别出背景音乐，上线前请确认音源授权。",
                "dedupe_key": "bgm:unknown",
                "evidence": [
                    {
                        "kind": "metadata",
                        "note": "ACRCloud 未匹配（code 1001），无法核验授权",
                    },
                    {
                        "kind": "timestamp",
                        "t_start": window["start"],
                        "t_end": window["end"],
                        "note": "首个未识别窗口",
                    },
                ],
            }
        )
    return signals
