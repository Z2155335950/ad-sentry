"""评测入口（等价于 `make eval`）：跑评测集、出报告与消融表（PROMPT §15）。

默认离线：`TOOLS_MODE=mock`、抽取结果复用缓存、VLM 基线读缓存。
输出 `eval_report.md` 与 `eval_report.json`。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from sqlmodel import Session, SQLModel, create_engine, select

from app.agent import vlm_baseline
from app.config import settings
from app.eval.metrics import EvalCase, summarize
from app.models import Asset, ToolCall
from app.pipeline import detect, extract, fuse
from app.pipeline.ingest import detect_media_type
from app.tools.base import ToolRuntime

ROOT = Path(__file__).resolve().parents[2]
EVAL_PATH = ROOT / "fixtures" / "eval.jsonl"
REPORT_MD = ROOT / "eval_report.md"
REPORT_JSON = ROOT / "eval_report.json"
DB_PATH = ROOT / "data" / "eval_run.db"

GROUP_LABELS = {
    "baseline_vlm": "纯 VLM 基线（缓存）",
    "tools_only": "工具层（无 Verifier）",
    "full": "完整链路（工具 + Verifier）",
    "full_planner_off": "完整链路（关闭 Planner 帧预算）",
}


def load_cases() -> list[EvalCase]:
    if not EVAL_PATH.exists():
        raise SystemExit(f"缺少评测集 {EVAL_PATH}，先运行 scripts/make_eval_set.py")
    cases = []
    for line in EVAL_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        cases.append(
            EvalCase(
                case_id=row["id"],
                path=row["path"],
                clean=bool(row.get("clean")),
                truth=row.get("truth") or [],
            )
        )
    return cases


def _engine():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    DB_PATH.unlink(missing_ok=True)
    engine = create_engine(f"sqlite:///{DB_PATH.as_posix()}")
    SQLModel.metadata.create_all(engine)
    return engine


def _prepare_assets(engine, cases: list[EvalCase]) -> dict[str, Asset]:
    assets: dict[str, Asset] = {}
    # 详情：Asset 要在后面的多个分组里复用，用 expire_on_commit=False + expunge 脱离会话
    with Session(engine, expire_on_commit=False) as session:
        for case in cases:
            path = ROOT / case.path
            if not path.exists():
                raise SystemExit(f"评测素材不存在: {path}")
            import hashlib

            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            asset = session.exec(select(Asset).where(Asset.sha256 == sha)).first()
            if asset is None:
                asset = Asset(
                    sha256=sha,
                    media_type=detect_media_type(path.name, None),
                    path=str(path),
                )
                session.add(asset)
                session.commit()
                session.refresh(asset)
            session.expunge(asset)
            assets[case.case_id] = asset
    return assets


def _extraction(asset: Asset) -> dict:
    if extract.has_extraction(asset.sha256):
        data = extract.load_extraction(asset.sha256)
        if data:
            return data
    return extract.extract_asset(asset)


def _to_finding(signal: dict) -> dict:
    level, _ = fuse.cap_level_without_anchor(signal["level"], signal.get("evidence", []))
    return {
        "category": signal["category"],
        "level": level,
        "confidence": signal.get("confidence"),
        "evidence": signal.get("evidence", []),
        "suggestion": signal.get("suggestion", ""),
    }


def _counters(session: Session) -> tuple[int, float]:
    rows = session.exec(select(ToolCall)).all()
    return len(rows), round(sum(row.cost_usd for row in rows), 6)


def run_group(engine, cases, assets, *, verifier: bool, planner: bool):
    settings.ablation_verifier = verifier
    settings.ablation_planner = planner
    results = []
    totals = {"candidates": 0, "calls": 0, "upgraded": 0, "skipped": 0}
    for case in cases:
        asset = assets[case.case_id]
        with Session(engine) as session:
            before_calls, before_cost = _counters(session)
            started = time.perf_counter()
            extraction = _extraction(asset)
            runtime = ToolRuntime(session, job_id=None)
            stats: dict = {}
            signals = detect.run(asset, extraction, runtime, stats=stats)
            elapsed = time.perf_counter() - started
            after_calls, after_cost = _counters(session)
        verifier_stats = stats.get("verifier") or {}
        for key in totals:
            totals[key] += int(verifier_stats.get(key) or 0)
        results.append(
            {
                "case": case,
                "findings": [_to_finding(signal) for signal in signals],
                "latency_s": round(elapsed, 3),
                "calls": after_calls - before_calls,
                "cost_usd": round(after_cost - before_cost, 6),
                "verifier": verifier_stats or None,
            }
        )
    return results, totals


def run_vlm_baseline(cases, assets) -> list[dict]:
    results = []
    for case in cases:
        asset = assets[case.case_id]
        claims = vlm_baseline.claims_for(asset.sha256)
        findings = [
            {
                "category": claim["category"],
                "level": claim.get("level", "P1"),
                "confidence": claim.get("confidence"),
                "evidence": claim.get("evidence", []),
                "suggestion": claim.get("suggestion", "VLM 基线缓存的判断"),
            }
            for claim in claims
        ]
        results.append(
            {
                "case": case,
                "findings": findings,
                "latency_s": 0.0,
                "calls": 0,
                "cost_usd": 0.0,
                "verifier": None,
            }
        )
    return results


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _interval(pair: tuple[float, float]) -> str:
    return f"{pair[0] * 100:.0f}–{pair[1] * 100:.0f}%"


def build_report(
    reports: dict[str, dict], cases: list[EvalCase], verifier_totals: dict | None = None
) -> str:
    verifier_totals = verifier_totals or {}
    clean = sum(1 for case in cases if case.clean)
    lines = [
        "# AdSentry 评测报告",
        "",
        f"- 生成时间：{datetime.now().isoformat(timespec='seconds')}",
        f"- 评测集：`fixtures/eval.jsonl`，共 **{len(cases)}** 条（干净 {clean} 条）",
        f"- 运行模式：`TOOLS_MODE={settings.tools_mode}`，抽取结果复用 `data/extracted/` 缓存",
        "- VLM 基线：`fixtures/cache/vlm_baseline.json` 缓存结果，不是实时调用",
        "",
        "## 总体对比",
        "",
        "| 组 | 用例 | P0/P1 预测 | 命中 | 真值 P0/P1 | 召回（Wilson） | 精确（Wilson） | "
        "干净集误报 | 外部调用 | 成本 | p50 | p90 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for key, summary in reports.items():
        lines.append(
            "| {label} | {cases} | {pred} | {hit} | {truth} | {recall} | {precision} | "
            "{fp}/{clean} | {calls} | ${cost:.4f} | {p50}s | {p90}s |".format(
                label=GROUP_LABELS[key],
                cases=summary["cases"],
                pred=summary["predicted_risk"],
                hit=summary["predicted_hit"],
                truth=summary["truth_risk"],
                recall=f"{_pct(summary['recall'])}（{_interval(summary['recall_wilson'])}）",
                precision=(
                    f"{_pct(summary['precision'])}"
                    f"（{_interval(summary['precision_wilson'])}）"
                ),
                fp=summary["clean_false_positive_cases"],
                clean=summary["clean_cases"],
                calls=summary["total_calls"],
                cost=summary["total_cost_usd"],
                p50=summary["latency_p50_s"],
                p90=summary["latency_p90_s"],
            )
        )

    lines += [
        "",
        "## 消融结论",
        "",
        f"- **Verifier**：低置信候选 {verifier_totals.get('candidates', 0)} 条，"
        f"二次检索 {verifier_totals.get('calls', 0)} 次，"
        f"置信升级 {verifier_totals.get('upgraded', 0)} 条。",
        "- **调用次数 / 成本**：`工具层` 与 `完整链路` 的差值就是 Verifier 的额外开销；",
        "  `关闭 Planner 帧预算` 一行展示了不做帧裁剪时的调用膨胀。",
        "- **干净集误报**：干净用例上出现任何 P0/P1 即记一次误报。",
        "- **植入集召回**：真值里等级为 P0/P1 的信号是否被同类别信号命中。",
        "- **VLM 基线为什么 0 命中**：它给不出可定位证据（bbox / 时间戳 / 参照物），",
        "  按 PROMPT §15 的匹配规则无法计入命中——这正是它不能单独支撑 P0/P1 的原因。",
        "",
        "## 分类别计数",
        "",
        "| 组 | 类别 | 真值 | 命中 | 预测 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for key, summary in reports.items():
        for category, stat in sorted(summary["per_category"].items()):
            lines.append(
                f"| {GROUP_LABELS[key]} | {category} | {stat['truth']} | "
                f"{stat['hit']} | {stat['pred']} |"
            )

    misses = reports["full"].get("missed_cases") or []
    fps = reports["full"].get("false_positive_cases") or []
    lines += [
        "",
        "## 完整链路的漏检与误报",
        "",
        f"- 漏检用例：{', '.join(misses) if misses else '无'}",
        f"- 干净集误报用例：{', '.join(fps) if fps else '无'}",
        "",
        "## 局限（必须如实说明）",
        "",
        "- 样本量小：整体 n < 15，分类别更少，**precision/recall 只作计数参考**，",
        "  不建议对外引用为稳定指标；表中附 Wilson 区间说明不确定性。",
        "- 工具层为 mock：外部调用次数与成本真实可测，但召回/误报反映的是 fixture 行为，",
        "  不是真实 API 表现。",
        "- **Verifier 在 mock 下通常不会提升置信**：二次检索对同一分镜会命中同一份缓存。",
        f"  本次从 {verifier_totals.get('candidates', 0)} 条低置信候选上做了 "
        f"{verifier_totals.get('calls', 0)} 次重搜，升级 {verifier_totals.get('upgraded', 0)} 条；"
        "  真实收益需要 live 凭证实测。调用次数差异是真实开销。",
        "- 纯 VLM 基线来自缓存（含一条人工标注的幻觉误报），用于说明「模型直读会编」，",
        "  不代表当前模型的真实能力。",
        "- 延迟包含抽取（首次会写缓存，后续命中缓存），不等同线上 SLA。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    settings.tools_mode = "mock"
    settings.asr_enabled = False
    settings.extract_enabled = True

    cases = load_cases()
    engine = _engine()
    assets = _prepare_assets(engine, cases)

    # 预热抽取缓存：否则第一组会把 OCR/抽帧耗时算进自己的延迟
    for case in cases:
        try:
            _extraction(assets[case.case_id])
        except Exception as exc:  # 抽取失败只影响该用例
            print(f"[WARN] 抽取失败 {case.case_id}: {exc}")

    tools_results, tools_verify = run_group(
        engine, cases, assets, verifier=False, planner=True
    )
    full_results, full_verify = run_group(
        engine, cases, assets, verifier=True, planner=True
    )
    planner_results, planner_verify = run_group(
        engine, cases, assets, verifier=True, planner=False
    )

    reports = {
        "baseline_vlm": summarize(run_vlm_baseline(cases, assets)),
        "tools_only": summarize(tools_results),
        "full": summarize(full_results),
        "full_planner_off": summarize(planner_results),
    }
    reports["tools_only"]["verifier"] = tools_verify
    reports["full"]["verifier"] = full_verify
    reports["full_planner_off"]["verifier"] = planner_verify

    REPORT_MD.write_text(build_report(reports, cases, full_verify), encoding="utf-8")
    REPORT_JSON.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "tools_mode": settings.tools_mode,
                "cases": [
                    {"id": c.case_id, "path": c.path, "clean": c.clean, "truth": c.truth}
                    for c in cases
                ],
                "groups": reports,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    full = reports["full"]
    print("[OK] 评测报告已生成: eval_report.md / eval_report.json")
    print(
        "     用例 {cases}（干净 {clean}）| 完整链路 P0/P1 召回 {hit}/{truth}，"
        "干净集误报 {fp}/{clean_cases} | 调用 {calls} 次".format(
            cases=full["cases"],
            clean=full["clean_cases"],
            hit=full["truth_hit"],
            truth=full["truth_risk"],
            fp=full["clean_false_positive_cases"],
            clean_cases=full["clean_cases"],
            calls=full["total_calls"],
        )
    )


if __name__ == "__main__":
    main()
