"""저장/조회 함수. A·B·C는 DB 대신 이 함수들만 호출한다. 담당: D"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from app.core.models import Brand, ChangeEvent, RawItem, SourceType, WeeklyReport
from app.infra import KST
from app.infra.storage.db import connect


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


def snapshot_counts(brand: Brand, source_types: list[SourceType]) -> dict[str, int]:
    """소스별 누적 저장 건수. 수집 전후 차이로 '이번 실행에서 몇 건 저장됐는지'를 잰다."""
    marks = ",".join("?" * len(source_types))
    with connect() as conn:
        rows = conn.execute(
            "SELECT source, COUNT(*) AS n FROM snapshots WHERE brand=? AND source IN "
            f"(SELECT DISTINCT source FROM raw_items WHERE brand=? AND source_type IN ({marks})) "
            "GROUP BY source",
            (brand.value, brand.value, *(t.value for t in source_types)),
        ).fetchall()
    return {r["source"]: r["n"] for r in rows}


def latest_collected_at(brand: Brand, source_type: SourceType) -> datetime | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT MAX(collected_at) AS ts FROM raw_items WHERE brand=? AND source_type=?",
            (brand.value, source_type.value),
        ).fetchone()
    return datetime.fromisoformat(row["ts"]) if row["ts"] else None


def purge_snapshots(keep_weeks: int = 8) -> int:
    """보관 기간이 지난 일자별 스냅샷 삭제 (raw_items 의 최신 상태는 남는다). 삭제 건수 반환."""
    cutoff = (datetime.now(KST).date() - timedelta(weeks=keep_weeks)).isoformat()
    with connect() as conn:
        return conn.execute("DELETE FROM snapshots WHERE collected_at < ?", (cutoff,)).rowcount


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
        rows = conn.execute("SELECT data FROM change_events").fetchall()
    events = [ChangeEvent.model_validate_json(r["data"]) for r in rows]
    return [e for e in events if e.category is None]


def events_of_week(week: str) -> list[ChangeEvent]:
    start, end = _week_range(week)
    with connect() as conn:
        rows = conn.execute(
            "SELECT data FROM change_events WHERE detected_at >= ? AND detected_at < ?", (start, end)
        ).fetchall()
    return [ChangeEvent.model_validate_json(r["data"]) for r in rows]


# ---------- Report (C 생성, D 발송) ----------
def save_report(report: WeeklyReport, *, sent: bool = True) -> None:
    """sent=False(드라이런·발송 실패)면 sent_at 을 비워 둔다."""
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO reports (week, tldr, data, sent_at) VALUES (?,?,?,?)",
            (report.week, report.tldr, report.model_dump_json(),
             datetime.now(KST).isoformat() if sent else None),
        )


def last_report_tldr(week: str) -> str | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT tldr FROM reports WHERE week < ? ORDER BY week DESC LIMIT 1", (week,)
        ).fetchone()
    return row["tldr"] if row else None


# ---------- LLM 사용량 (C 기록) ----------
def save_llm_call(model: str, input_tokens: int, output_tokens: int) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO llm_calls (model, input_tokens, output_tokens, called_at) VALUES (?,?,?,?)",
            (model, input_tokens, output_tokens, datetime.now(KST).isoformat()),
        )


def llm_usage(week: str) -> dict[str, dict[str, int]]:
    """주차별 모델별 사용량: {"claude-...": {"calls": 3, "input_tokens": 1200, "output_tokens": 400}}"""
    start, end = _week_range(week)
    with connect() as conn:
        rows = conn.execute(
            "SELECT model, COUNT(*) AS calls, SUM(input_tokens) AS input_tokens, "
            "SUM(output_tokens) AS output_tokens FROM llm_calls "
            "WHERE called_at >= ? AND called_at < ? GROUP BY model", (start, end),
        ).fetchall()
    return {r["model"]: {k: r[k] for k in ("calls", "input_tokens", "output_tokens")} for r in rows}
