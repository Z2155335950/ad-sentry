"""任务完成后把风险卡片推回飞书（PROMPT §12），可选写多维表格。

推送失败不影响任务本身：只记录日志并把事件状态标为 notify_failed。
"""

from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.api.review import make_review_url_for
from app.config import settings
from app.db import engine
from app.feishu import client
from app.models import (
    LEVEL_ORDER,
    Asset,
    Evidence,
    FeishuEvent,
    Finding,
    Job,
    utcnow,
)
from app.pipeline.report import build_card

logger = logging.getLogger(__name__)

NO_RISK_TEXT = "未发现风险信号。本结果为基于公开信息的风险提示，不构成法律意见。"


def _findings_payload(session: Session, asset_id: int) -> list[dict]:
    findings = session.exec(
        select(Finding).where(Finding.asset_id == asset_id).order_by(Finding.id)
    ).all()
    payload = []
    for finding in findings:
        evidences = session.exec(
            select(Evidence).where(Evidence.finding_id == finding.id).order_by(Evidence.id)
        ).all()
        payload.append(
            {
                "id": finding.id,
                "category": finding.category,
                "level": finding.level,
                "confidence": finding.confidence,
                "suggestion": finding.suggestion,
                "review_status": finding.review_status,
                "evidence": [ev.model_dump() for ev in evidences],
            }
        )
    payload.sort(key=lambda item: (LEVEL_ORDER.get(item["level"], 9), item["id"]))
    return payload


def notify_job_done(job_id: int) -> None:
    with Session(engine) as session:
        event = session.exec(
            select(FeishuEvent).where(FeishuEvent.job_id == job_id)
        ).first()
        if event is None or event.status != "queued":
            return
        job = session.get(Job, job_id)
        if job is None:
            return
        asset = session.get(Asset, job.asset_id)
        payload = _findings_payload(session, job.asset_id)

        try:
            if not payload:
                client.send_text(event.chat_id, NO_RISK_TEXT)
            else:
                base = settings.public_base_url.rstrip("/")
                urls = {item["id"]: make_review_url_for(base, item["id"]) for item in payload}
                client.send_card(event.chat_id, build_card(payload, urls))
                client.append_bitable(
                    {
                        "素材": (asset.sha256[:16] if asset else ""),
                        "媒体类型": asset.media_type if asset else "",
                        "最高等级": payload[0]["level"],
                        "信号数": len(payload),
                        "复核链接": urls.get(payload[0]["id"], ""),
                        "检查时间": utcnow().isoformat(timespec="seconds"),
                    }
                )
        except Exception as exc:
            logger.warning("飞书卡片推送失败 job=%s: %s", job_id, exc)
            event.status = "notify_failed"
            event.note = str(exc)[:300]
            session.add(event)
            session.commit()
            return

        event.status = "processed"
        session.add(event)
        session.commit()
