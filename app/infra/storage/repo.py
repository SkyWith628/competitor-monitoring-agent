"""저장/조회 함수. A·B·C는 DB 대신 이 함수들만 호출한다. 담당: D"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.models import Brand, ChangeEvent, RawItem, SourceType, WeeklyReport
from app.infra.storage.db import connect

KST = ZoneInfo("Asia/Seoul")


def _week_range(week: str) -> tuple[str, str]:
    """'2026-W47' → (월요일 00:00, 다음 월요일 00:00) ISO 문자열"""
    year, w = week.split("-W")
    start = date.fromisocalendar(int(year), int(w), 1)
    return start.isoformat(), (start + timedelta(days=7)).isoformat()


# ---------- RawItem (A·B) ----------
def save_items(items: list[RawItem]) -> None:
    with connect() as conn:
        for i in items:
            data, ts = i.model_dump_json(), i.collected_at.isoformat()
            conn.execute(
                "INSERT OR REPLACE INTO raw_items VALUES (?,?,?,?,?,?)",
                (i.brand.value, i.source, i.external_id, i.source_type.value, data, ts),
            )
            conn.execute(
                "INSERT INTO snapshots (brand, source, external_id, data, collected_at) VALUES (?,?,?,?,?)",
                (i.brand.value, i.source, i.external_id, data, ts),
            )


def latest_items(brand: Brand, source: str) -> dict[str, RawItem]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT external_id, data FROM raw_items WHERE brand=? AND source=?", (brand.value, source)
        ).fetchall()
    return {r["external_id"]: RawItem.model_validate_json(r["data"]) for r in rows}


def exists(item: RawItem) -> bool:
    with connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM raw_items WHERE brand=? AND source=? AND external_id=?",
            (item.brand.value, item.source, item.external_id),
        ).fetchone()
    return row is not None


def recent_posts(brand: Brand, source: str, limit: int = 30) -> list[RawItem]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT data FROM raw_items WHERE brand=? AND source=? AND source_type=? "
            "ORDER BY collected_at DESC LIMIT ?",
            (brand.value, source, SourceType.SNS_POST.value, limit),
        ).fetchall()
    return [RawItem.model_validate_json(r["data"]) for r in rows]


# ---------- ChangeEvent (A·B 저장, C 조회·갱신) ----------
def save_events(events: list[ChangeEvent]) -> None:
    with connect() as conn:
        for e in events:
            cur = conn.execute(
                "INSERT INTO change_events (brand, data, detected_at) VALUES (?,?,?)",
                (e.brand.value, "{}", e.detected_at.isoformat()),
            )
            e.id = cur.lastrowid
            conn.execute("UPDATE change_events SET data=? WHERE id=?", (e.model_dump_json(), e.id))


def update_events(events: list[ChangeEvent]) -> None:
    """C가 분류 결과(category, importance, llm_note)를 채운 뒤 호출."""
    with connect() as conn:
        for e in events:
            conn.execute("UPDATE change_events SET data=? WHERE id=?", (e.model_dump_json(), e.id))


def unclassified_events() -> list[ChangeEvent]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT data FROM change_events WHERE json_extract(data, '$.category') IS NULL ORDER BY id"
        ).fetchall()
    return [ChangeEvent.model_validate_json(r["data"]) for r in rows]


def events_of_week(week: str) -> list[ChangeEvent]:
    start, end = _week_range(week)
    with connect() as conn:
        rows = conn.execute(
            "SELECT data FROM change_events WHERE detected_at >= ? AND detected_at < ?", (start, end)
        ).fetchall()
    return [ChangeEvent.model_validate_json(r["data"]) for r in rows]


# ---------- Report (C 생성, D 발송) ----------
def save_report(report: WeeklyReport, sent: bool = True) -> None:
    """sent=False(dry-run)면 발송 시각을 새로 찍지 않고, 이미 발송된 기록이 있으면 그 시각을 유지한다."""
    sent_at = datetime.now(KST).isoformat() if sent else None
    with connect() as conn:
        conn.execute(
            "INSERT INTO reports (week, tldr, data, sent_at) VALUES (?,?,?,?) "
            "ON CONFLICT(week) DO UPDATE SET tldr=excluded.tldr, data=excluded.data, "
            "sent_at=COALESCE(excluded.sent_at, reports.sent_at)",
            (report.week, report.tldr, report.model_dump_json(), sent_at),
        )


def last_report_tldr(week: str) -> str | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT tldr FROM reports WHERE week < ? ORDER BY week DESC LIMIT 1", (week,)
        ).fetchone()
    return row["tldr"] if row else None
