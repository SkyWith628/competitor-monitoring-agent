"""저장/조회 함수. A·B·C는 DB 대신 이 함수들만 호출한다. 담당: D"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import and_, delete, func, insert, select, update

from app.core.models import Brand, ChangeEvent, RawItem, SourceType, WeeklyReport
from app.infra import KST
from app.infra.storage.db import change_events, connect, llm_calls, raw_items, reports, snapshots


def _week_range(week: str) -> tuple[str, str]:
    """'2026-W47' → (월요일 00:00, 다음 월요일 00:00) ISO 문자열"""
    year, w = week.split("-W")
    start = date.fromisocalendar(int(year), int(w), 1)
    return start.isoformat(), (start + timedelta(days=7)).isoformat()


def _item_key(brand: Brand, source: str, external_id: str):
    return and_(raw_items.c.brand == brand.value, raw_items.c.source == source,
                raw_items.c.external_id == external_id)


# ---------- RawItem (A·B) ----------
def save_items(items: list[RawItem]) -> None:
    if not items:
        return
    with connect() as conn:
        rows = []
        for i in items:
            row = {"brand": i.brand.value, "source": i.source, "external_id": i.external_id,
                   "data": i.model_dump_json(), "collected_at": i.collected_at.isoformat()}
            conn.execute(delete(raw_items).where(_item_key(i.brand, i.source, i.external_id)))
            conn.execute(insert(raw_items).values(**row, source_type=i.source_type.value))
            rows.append(row)
        conn.execute(insert(snapshots), rows)


def latest_items(brand: Brand, source: str) -> dict[str, RawItem]:
    with connect() as conn:
        rows = conn.execute(
            select(raw_items.c.external_id, raw_items.c.data)
            .where(raw_items.c.brand == brand.value, raw_items.c.source == source)
        ).all()
    return {r.external_id: RawItem.model_validate_json(r.data) for r in rows}


def exists(item: RawItem) -> bool:
    with connect() as conn:
        row = conn.execute(
            select(raw_items.c.brand).where(_item_key(item.brand, item.source, item.external_id))
        ).first()
    return row is not None


def recent_posts(brand: Brand, source: str, limit: int = 30) -> list[RawItem]:
    with connect() as conn:
        rows = conn.execute(
            select(raw_items.c.data)
            .where(raw_items.c.brand == brand.value, raw_items.c.source == source,
                   raw_items.c.source_type == SourceType.SNS_POST.value)
            .order_by(raw_items.c.collected_at.desc()).limit(limit)
        ).all()
    return [RawItem.model_validate_json(r.data) for r in rows]


def snapshot_counts(brand: Brand, source_types: list[SourceType]) -> dict[str, int]:
    """소스별 누적 저장 건수. 수집 전후 차이로 '이번 실행에서 몇 건 저장됐는지'를 잰다."""
    sources = (select(raw_items.c.source)
               .where(raw_items.c.brand == brand.value,
                      raw_items.c.source_type.in_([t.value for t in source_types])))
    with connect() as conn:
        rows = conn.execute(
            select(snapshots.c.source, func.count().label("n"))
            .where(snapshots.c.brand == brand.value, snapshots.c.source.in_(sources))
            .group_by(snapshots.c.source)
        ).all()
    return {r.source: r.n for r in rows}


def latest_collected_at(brand: Brand, source_type: SourceType) -> datetime | None:
    with connect() as conn:
        ts = conn.execute(
            select(func.max(raw_items.c.collected_at))
            .where(raw_items.c.brand == brand.value, raw_items.c.source_type == source_type.value)
        ).scalar()
    return datetime.fromisoformat(ts) if ts else None


def purge_snapshots(keep_weeks: int = 8) -> int:
    """보관 기간이 지난 일자별 스냅샷 삭제 (raw_items 의 최신 상태는 남는다). 삭제 건수 반환."""
    cutoff = (datetime.now(KST).date() - timedelta(weeks=keep_weeks)).isoformat()
    with connect() as conn:
        return conn.execute(delete(snapshots).where(snapshots.c.collected_at < cutoff)).rowcount


# ---------- ChangeEvent (A·B 저장, C 조회·갱신) ----------
def save_events(events: list[ChangeEvent]) -> None:
    with connect() as conn:
        for e in events:
            result = conn.execute(insert(change_events).values(
                brand=e.brand.value, data="{}", detected_at=e.detected_at.isoformat()))
            e.id = result.inserted_primary_key[0]
            conn.execute(update(change_events).where(change_events.c.id == e.id)
                         .values(data=e.model_dump_json()))


def update_events(events: list[ChangeEvent]) -> None:
    """C가 분류 결과(category, importance, llm_note)를 채운 뒤 호출."""
    with connect() as conn:
        for e in events:
            conn.execute(update(change_events).where(change_events.c.id == e.id)
                         .values(data=e.model_dump_json()))


def unclassified_events() -> list[ChangeEvent]:
    with connect() as conn:
        rows = conn.execute(select(change_events.c.data).order_by(change_events.c.id)).all()
    events = [ChangeEvent.model_validate_json(r.data) for r in rows]
    return [e for e in events if e.category is None]


def events_of_week(week: str) -> list[ChangeEvent]:
    start, end = _week_range(week)
    with connect() as conn:
        rows = conn.execute(
            select(change_events.c.data)
            .where(change_events.c.detected_at >= start, change_events.c.detected_at < end)
            .order_by(change_events.c.id)
        ).all()
    return [ChangeEvent.model_validate_json(r.data) for r in rows]


# ---------- Report (C 생성, D 발송) ----------
def save_report(report: WeeklyReport, *, sent: bool = True) -> None:
    """sent=False(드라이런·발송 실패)면 sent_at 을 비워 둔다."""
    with connect() as conn:
        conn.execute(delete(reports).where(reports.c.week == report.week))
        conn.execute(insert(reports).values(
            week=report.week, tldr=report.tldr, data=report.model_dump_json(),
            sent_at=datetime.now(KST).isoformat() if sent else None))


def report_sent_at(week: str) -> str | None:
    with connect() as conn:
        return conn.execute(select(reports.c.sent_at).where(reports.c.week == week)).scalar()


def last_report_tldr(week: str) -> str | None:
    with connect() as conn:
        return conn.execute(
            select(reports.c.tldr).where(reports.c.week < week).order_by(reports.c.week.desc()).limit(1)
        ).scalar()


# ---------- LLM 사용량 (C 기록) ----------
def save_llm_call(model: str, input_tokens: int, output_tokens: int) -> None:
    with connect() as conn:
        conn.execute(insert(llm_calls).values(model=model, input_tokens=input_tokens,
                                              output_tokens=output_tokens,
                                              called_at=datetime.now(KST).isoformat()))


def llm_usage(week: str) -> dict[str, dict[str, int]]:
    """주차별 모델별 사용량: {"claude-...": {"calls": 3, "input_tokens": 1200, "output_tokens": 400}}"""
    start, end = _week_range(week)
    with connect() as conn:
        rows = conn.execute(
            select(llm_calls.c.model, func.count().label("calls"),
                   func.sum(llm_calls.c.input_tokens).label("input_tokens"),
                   func.sum(llm_calls.c.output_tokens).label("output_tokens"))
            .where(llm_calls.c.called_at >= start, llm_calls.c.called_at < end)
            .group_by(llm_calls.c.model)
        ).all()
    return {r.model: {"calls": r.calls, "input_tokens": int(r.input_tokens),
                      "output_tokens": int(r.output_tokens)} for r in rows}
