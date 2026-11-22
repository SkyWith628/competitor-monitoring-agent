"""저장/조회 함수. A·B·C는 DB 대신 이 함수들만 호출한다. 담당: D

SQL은 SQLite·PostgreSQL 양쪽에서 돌아야 한다. 시각은 ISO 문자열로 저장하고 문자열 비교로 거른다.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, insert, select, text, update

from app.core.models import Brand, ChangeEvent, RawItem, SourceType, WeeklyReport
from app.infra.storage.db import change_events, connect, llm_calls, raw_items, reports, snapshots, upsert

KST = ZoneInfo("Asia/Seoul")


def _week_range(week: str) -> tuple[str, str]:
    """'2026-W47' → (월요일 00:00, 다음 월요일 00:00) ISO 문자열"""
    year, w = week.split("-W")
    start = date.fromisocalendar(int(year), int(w), 1)
    return start.isoformat(), (start + timedelta(days=7)).isoformat()


def _naive_iso(dt: datetime) -> str:
    """저장된 시각(datetime.now(), tz 없음)과 비교할 수 있게 로컬 시각의 naive ISO 문자열로."""
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.isoformat()


def _ago(**delta) -> str:
    """지금으로부터 delta 전 시각 (로컬 naive ISO)."""
    return _naive_iso(datetime.now(UTC) - timedelta(**delta))


# ---------- RawItem (A·B) ----------
def save_items(items: list[RawItem]) -> None:
    with connect() as conn:
        for i in items:
            key = {"brand": i.brand.value, "source": i.source, "external_id": i.external_id}
            data, ts = i.model_dump_json(), i.collected_at.isoformat()
            latest = {"source_type": i.source_type.value, "data": data, "collected_at": ts}
            conn.execute(
                upsert(raw_items).values(**key, **latest)
                .on_conflict_do_update(index_elements=list(key), set_=latest)
            )
            conn.execute(insert(snapshots).values(**key, data=data, collected_at=ts))


def latest_items(brand: Brand, source: str) -> dict[str, RawItem]:
    with connect() as conn:
        rows = conn.execute(
            select(raw_items.c.external_id, raw_items.c.data)
            .where(raw_items.c.brand == brand.value, raw_items.c.source == source)
        ).fetchall()
    return {r.external_id: RawItem.model_validate_json(r.data) for r in rows}


def exists(item: RawItem) -> bool:
    with connect() as conn:
        row = conn.execute(
            select(raw_items.c.external_id).where(
                raw_items.c.brand == item.brand.value,
                raw_items.c.source == item.source,
                raw_items.c.external_id == item.external_id,
            )
        ).fetchone()
    return row is not None


def recent_posts(brand: Brand, source: str, limit: int = 30) -> list[RawItem]:
    with connect() as conn:
        rows = conn.execute(
            select(raw_items.c.data)
            .where(raw_items.c.brand == brand.value, raw_items.c.source == source,
                   raw_items.c.source_type == SourceType.SNS_POST.value)
            .order_by(raw_items.c.collected_at.desc())
            .limit(limit)
        ).fetchall()
    return [RawItem.model_validate_json(r.data) for r in rows]


def count_items_since(brand: Brand, source_types: list[SourceType], since: datetime) -> int:
    """since 이후 수집된(collected_at >= since) 브랜드의 raw_items 수. 수집 0건 경고 등에 쓴다."""
    if not source_types:
        return 0
    with connect() as conn:
        return conn.execute(
            select(func.count()).select_from(raw_items).where(
                raw_items.c.brand == brand.value,
                raw_items.c.source_type.in_([t.value for t in source_types]),
                raw_items.c.collected_at >= _naive_iso(since),
            )
        ).scalar_one()


def item_counts() -> dict[str, dict[str, int]]:
    """브랜드 → source_type → raw_items 건수 (값은 enum의 value 문자열)."""
    with connect() as conn:
        rows = conn.execute(
            select(raw_items.c.brand, raw_items.c.source_type, func.count())
            .group_by(raw_items.c.brand, raw_items.c.source_type)
        ).fetchall()
    out: dict[str, dict[str, int]] = {}
    for brand, source_type, n in rows:
        out.setdefault(brand, {})[source_type] = int(n)
    return out


def prune_snapshots(weeks: int = 8) -> int:
    """N주보다 오래된 스냅샷을 지우고 지운 건수를 돌려준다. raw_items(최신 상태)는 건드리지 않는다."""
    with connect() as conn:
        return conn.execute(delete(snapshots).where(snapshots.c.collected_at < _ago(weeks=weeks))).rowcount


# ---------- ChangeEvent (A·B 저장, C 조회·갱신) ----------
def save_events(events: list[ChangeEvent]) -> None:
    with connect() as conn:
        for e in events:
            result = conn.execute(
                insert(change_events)
                .values(brand=e.brand.value, data="{}", detected_at=e.detected_at.isoformat())
            )
            e.id = result.inserted_primary_key[0]  # SQLite는 lastrowid, PostgreSQL은 RETURNING
            _set_event_data(conn, e)


def _set_event_data(conn, e: ChangeEvent) -> None:
    conn.execute(update(change_events).where(change_events.c.id == e.id).values(data=e.model_dump_json()))


def update_events(events: list[ChangeEvent]) -> None:
    """C가 분류 결과(category, importance, llm_note)를 채운 뒤 호출."""
    with connect() as conn:
        for e in events:
            _set_event_data(conn, e)


def unclassified_events() -> list[ChangeEvent]:
    with connect() as conn:
        # data는 TEXT라 방언별로 JSON을 꺼낸다. 둘 다 JSON null을 SQL NULL로 돌려준다.
        pg = conn.dialect == "postgresql"
        category = "data::json ->> 'category'" if pg else "json_extract(data, '$.category')"
        rows = conn.execute(
            select(change_events.c.data).where(text(f"{category} IS NULL")).order_by(change_events.c.id)
        ).fetchall()
    return [ChangeEvent.model_validate_json(r.data) for r in rows]


def events_of_week(week: str) -> list[ChangeEvent]:
    start, end = _week_range(week)
    with connect() as conn:
        rows = conn.execute(
            select(change_events.c.data)
            .where(change_events.c.detected_at >= start, change_events.c.detected_at < end)
            .order_by(change_events.c.id)
        ).fetchall()
    return [ChangeEvent.model_validate_json(r.data) for r in rows]


def recent_events(days: int = 30) -> list[ChangeEvent]:
    """최근 N일 이벤트, 최신순."""
    with connect() as conn:
        rows = conn.execute(
            select(change_events.c.data)
            .where(change_events.c.detected_at >= _ago(days=days))
            .order_by(change_events.c.detected_at.desc(), change_events.c.id.desc())
        ).fetchall()
    return [ChangeEvent.model_validate_json(r.data) for r in rows]


# ---------- Report (C 생성, D 발송) ----------
def save_report(report: WeeklyReport, sent: bool = True) -> None:
    """sent=False(dry-run)면 발송 시각을 새로 찍지 않고, 이미 발송된 기록이 있으면 그 시각을 유지한다."""
    sent_at = datetime.now(KST).isoformat() if sent else None
    stmt = upsert(reports).values(
        week=report.week, tldr=report.tldr, data=report.model_dump_json(), sent_at=sent_at
    )
    with connect() as conn:
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=["week"],
                set_={
                    "tldr": stmt.excluded.tldr,
                    "data": stmt.excluded.data,
                    "sent_at": func.coalesce(stmt.excluded.sent_at, reports.c.sent_at),
                },
            )
        )


def last_report_tldr(week: str) -> str | None:
    with connect() as conn:
        row = conn.execute(
            select(reports.c.tldr).where(reports.c.week < week).order_by(reports.c.week.desc()).limit(1)
        ).fetchone()
    return row.tldr if row else None


def get_report(week: str) -> WeeklyReport | None:
    with connect() as conn:
        row = conn.execute(select(reports.c.data).where(reports.c.week == week)).fetchone()
    return WeeklyReport.model_validate_json(row.data) if row else None


def list_reports(limit: int = 12) -> list[WeeklyReport]:
    """저장된 리포트, 최신 주차부터."""
    with connect() as conn:
        rows = conn.execute(select(reports.c.data).order_by(reports.c.week.desc()).limit(limit)).fetchall()
    return [WeeklyReport.model_validate_json(r.data) for r in rows]


# ---------- LLM 사용량 (C 기록, D 조회) ----------
def save_llm_call(model: str, input_tokens: int, output_tokens: int) -> None:
    with connect() as conn:
        conn.execute(
            insert(llm_calls).values(model=model, input_tokens=input_tokens, output_tokens=output_tokens,
                                     called_at=_ago())
        )


def llm_usage(days: int = 30) -> list[dict]:
    """최근 N일 모델별 호출 수·토큰 합계. [{"model", "calls", "input_tokens", "output_tokens"}]"""
    with connect() as conn:
        rows = conn.execute(
            select(llm_calls.c.model, func.count(), func.coalesce(func.sum(llm_calls.c.input_tokens), 0),
                   func.coalesce(func.sum(llm_calls.c.output_tokens), 0))
            .where(llm_calls.c.called_at >= _ago(days=days))
            .group_by(llm_calls.c.model)
            .order_by(llm_calls.c.model)
        ).fetchall()
    return [
        {"model": m, "calls": int(n), "input_tokens": int(i), "output_tokens": int(o)} for m, n, i, o in rows
    ]
