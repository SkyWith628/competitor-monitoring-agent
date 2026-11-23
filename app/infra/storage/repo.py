"""저장/조회 함수. A·B·C는 DB를 직접 만지지 않고 이 파일의 함수만 호출한다. 담당: D

누가 무엇을 쓰는가
    A·B (수집)   save_items → latest_items / exists / recent_posts 로 이전 상태와 비교 → save_events
    C (분석)     unclassified_events → (LLM 분류) → update_events,  events_of_week,  last_report_tldr,
                 save_llm_call
    D (발송·운영) save_report, report_channels_sent / mark_report_sent, pending_urgent / mark_urgent_sent,
                 그리고 status·대시보드용 조회 함수들

테이블 (정의는 db.py)
    raw_items      항목별 "최신 상태" 한 줄. 키는 (brand, source, external_id)
    snapshots      수집할 때마다 쌓이는 원본 이력. prune_snapshots 가 8주 지난 것을 지운다
    change_events  변화 감지 결과. C가 분류하면 같은 줄의 data 가 갱신된다
    reports        주차별 리포트 한 줄 + 발송 시각
    report_sends   리포트가 어느 채널(슬랙·메일)로 나갔는지 — 재실행 때 중복 발송 방지
    urgent_alerts  긴급 알림을 보낸 이벤트 — 실패한 알림만 다시 보내기 위해
    llm_calls      LLM 호출별 토큰 수

알아둘 규칙
    - 모델(RawItem, ChangeEvent, WeeklyReport)은 JSON 문자열로 통째로 `data` 컬럼에 넣는다.
      app/core/models.py 에 필드가 추가돼도 테이블을 바꿀 필요가 없다.
    - 시각은 ISO 문자열로 저장하고 문자열 비교로 범위를 거른다 ("2026-11-22T06:00:00").
      collected_at·detected_at 은 모델이 datetime.now() 로 찍은 "시간대 없는 한국 시각"이다
      (CLI가 실행 때 시간대를 한국으로 고정한다 → app/infra/timeutil.py).
    - SQL은 SQLite(로컬·테스트)와 PostgreSQL(운영) 양쪽에서 돌아야 한다. 방언이 갈리는 곳은
      db.upsert() 와 _unclassified() 두 군데뿐이다.
    - 함수 시그니처를 바꿀 때는 호출하는 쪽(A·B·C)에 먼저 알린다.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, insert, select, text, update

from app.core.models import Brand, ChangeEvent, RawItem, SourceType, WeeklyReport
from app.infra.storage.db import (
    change_events,
    connect,
    llm_calls,
    raw_items,
    report_sends,
    reports,
    snapshots,
    upsert,
    urgent_alerts,
)

KST = ZoneInfo("Asia/Seoul")


def _week_range(week: str) -> tuple[str, str]:
    """'2026-W47' → ('2026-11-16', '2026-11-23'): 그 주 월요일과 다음 주 월요일의 ISO 날짜.

    "start <= detected_at < end" 문자열 비교에 쓴다. '2026-11-16T09:00:00' >= '2026-11-16' 이 참이라
    날짜만 있어도 그날 00:00 부터로 동작한다.
    """
    year, w = week.split("-W")
    start = date.fromisocalendar(int(year), int(w), 1)
    return start.isoformat(), (start + timedelta(days=7)).isoformat()


def _naive_iso(dt: datetime) -> str:
    """저장된 시각과 비교할 수 있는 문자열로 바꾼다: 시간대 정보를 떼어낸 로컬(한국) 시각의 ISO 문자열.

    모델은 datetime.now() (시간대 없음)로 시각을 찍으므로, 시간대가 붙은 값이 들어오면 로컬 시각으로
    옮긴 뒤 시간대를 떼어 같은 기준으로 맞춘다.
    """
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt.isoformat()


def _ago(**delta) -> str:
    """지금으로부터 delta 전 시각 (로컬 naive ISO)."""
    return _naive_iso(datetime.now(UTC) - timedelta(**delta))


# ---------- RawItem (A·B) ----------
def save_items(items: list[RawItem]) -> None:
    """수집한 항목을 저장한다. A·B의 파이프라인이 수집 직후 호출.

    항목마다 두 가지를 한다:
      1. raw_items 의 최신 상태를 덮어쓴다 (같은 (brand, source, external_id) 가 있으면 갱신).
      2. snapshots 에 이번 수집분을 한 줄 추가한다 (이력).

    주의: 전일 대비 diff 를 하려면 latest_items() 로 이전 상태를 **먼저** 읽고 나서 호출해야 한다.
    저장하고 나면 이전 상태는 raw_items 에서 사라진다.
    """
    with connect() as conn:
        for i in items:
            # 이 세 값이 항목의 신원이다. 같은 키가 다시 오면 "같은 상품·기사·게시물의 새 상태"로 본다
            key = {"brand": i.brand.value, "source": i.source, "external_id": i.external_id}
            data, ts = i.model_dump_json(), i.collected_at.isoformat()
            latest = {"source_type": i.source_type.value, "data": data, "collected_at": ts}
            conn.execute(
                upsert(raw_items).values(**key, **latest)
                .on_conflict_do_update(index_elements=list(key), set_=latest)
            )
            conn.execute(insert(snapshots).values(**key, data=data, collected_at=ts))


def latest_items(brand: Brand, source: str) -> dict[str, RawItem]:
    """이 브랜드·소스의 항목별 최신 상태. {external_id: RawItem}

    A의 differ 가 "어제 본 상태"로 쓴다. 예: latest_items(Brand.SPAO, "spao.com")["P123"].price
    한 번도 수집한 적이 없으면 빈 dict.
    """
    with connect() as conn:
        rows = conn.execute(
            select(raw_items.c.external_id, raw_items.c.data)
            .where(raw_items.c.brand == brand.value, raw_items.c.source == source)
        ).fetchall()
    return {r.external_id: RawItem.model_validate_json(r.data) for r in rows}


def exists(item: RawItem) -> bool:
    """같은 (brand, source, external_id) 항목이 이미 저장돼 있는지. B가 "새 기사·새 게시물인가"를 볼 때 쓴다."""
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
    """이 계정의 최근 SNS 게시물 (수집 시각 최신순, 최대 limit 개).

    B의 spike.py 가 반응 급증의 기준선(평균)을 구할 때 쓴다. source 는 "instagram"·"youtube" 등.
    """
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


def silent_sources(brand: Brand, source_types: list[SourceType], since: datetime) -> list[str]:
    """전에는 수집되던 소스인데 since 이후로는 한 건도 들어오지 않은 소스 이름들.

    한 브랜드에 소스가 여럿이면(인스타그램·유튜브) 하나가 죽어도 합계는 0이 아니라서, 소스별로 따로 본다.
    """
    if not source_types:
        return []
    with connect() as conn:
        rows = conn.execute(
            select(raw_items.c.source, func.max(raw_items.c.collected_at)).where(
                raw_items.c.brand == brand.value,
                raw_items.c.source_type.in_([t.value for t in source_types]),
            ).group_by(raw_items.c.source).order_by(raw_items.c.source)
        ).fetchall()
    # 소스별 "마지막으로 수집된 시각"이 이번 실행 시작(since)보다 이르면, 이번에는 한 건도 못 가져온 것이다
    cutoff = _naive_iso(since)
    return [source for source, latest in rows if latest < cutoff]


def source_freshness() -> list[dict]:
    """소스별 수집 현황: [{"brand", "source", "source_type", "items", "last_collected_at"}].

    last_collected_at 은 저장된 그대로의 ISO 문자열(시간대 없는 한국 시각). 브랜드·소스 순.
    "수집이 아직 살아 있는가"를 status 명령과 대시보드가 이 값으로 보여준다.
    """
    with connect() as conn:
        rows = conn.execute(
            select(raw_items.c.brand, raw_items.c.source, raw_items.c.source_type, func.count(),
                   func.max(raw_items.c.collected_at))
            .group_by(raw_items.c.brand, raw_items.c.source, raw_items.c.source_type)
            .order_by(raw_items.c.brand, raw_items.c.source, raw_items.c.source_type)
        ).fetchall()
    return [{"brand": brand, "source": source, "source_type": source_type, "items": int(n),
             "last_collected_at": latest} for brand, source, source_type, n, latest in rows]


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
    """변화 이벤트를 저장하고, 넘겨받은 객체의 `id` 를 채운다. A·B가 변화 감지 직후 호출.

    id 는 DB가 매기므로 "빈 data 로 한 줄 넣기 → id 받기 → id 가 들어간 JSON 으로 갱신" 두 단계다.
    호출 뒤에는 events[0].id 가 정수다. 리포트의 근거 인용([#12])이 이 id 를 가리킨다.
    """
    with connect() as conn:
        for e in events:
            result = conn.execute(
                insert(change_events)
                .values(brand=e.brand.value, data="{}", detected_at=e.detected_at.isoformat())
            )
            e.id = result.inserted_primary_key[0]  # SQLite는 lastrowid, PostgreSQL은 RETURNING
            _set_event_data(conn, e)


def _set_event_data(conn, e: ChangeEvent) -> None:
    """이벤트 한 건의 JSON 을 통째로 갱신한다 (저장 직후와 분류 결과 반영에 공통)."""
    conn.execute(update(change_events).where(change_events.c.id == e.id).values(data=e.model_dump_json()))


def update_events(events: list[ChangeEvent]) -> None:
    """C가 분류 결과(category, importance, llm_note)를 채운 뒤 호출."""
    with connect() as conn:
        for e in events:
            _set_event_data(conn, e)


def _unclassified():
    """분류 전(category 가 JSON null) 이벤트 조건. data 는 TEXT 라 방언별로 JSON 을 꺼낸다."""
    from app.infra.storage.db import is_postgres

    category = "data::json ->> 'category'" if is_postgres() else "json_extract(data, '$.category')"
    return text(f"{category} IS NULL")  # 둘 다 JSON null 을 SQL NULL 로 돌려준다


def unclassified_events() -> list[ChangeEvent]:
    """아직 분류되지 않은(category 가 비어 있는) 이벤트 전부, 오래된 순. C의 classify 가 입력으로 쓴다.

    분류가 실패한 날의 이벤트도 다음 실행에서 그대로 다시 나온다.
    """
    with connect() as conn:
        rows = conn.execute(
            select(change_events.c.data).where(_unclassified()).order_by(change_events.c.id)
        ).fetchall()
    return [ChangeEvent.model_validate_json(r.data) for r in rows]


def unclassified_count() -> int:
    """분류를 기다리는 이벤트 수 (운영 상태 확인용)."""
    with connect() as conn:
        return int(conn.execute(
            select(func.count()).select_from(change_events).where(_unclassified())
        ).scalar_one())


def events_of_week(week: str) -> list[ChangeEvent]:
    """그 주(월요일 00:00 ~ 다음 월요일 00:00, 한국 시간)에 감지된 이벤트, id 순.

    week 는 ISO 주차 문자열 "2026-W47". 분류 여부와 무관하게 전부 돌려주므로, 리포트에 넣을 때는
    C 쪽에서 category 가 있는 것만 고른다.
    """
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


def pending_urgent(days: int = 7) -> list[ChangeEvent]:
    """중요도 5로 분류됐는데 아직 긴급 알림이 나가지 않은 최근 N일 이벤트 (오래된 순)."""
    with connect() as conn:
        done = {r.event_id for r in conn.execute(select(urgent_alerts.c.event_id)).fetchall()}
    # 최근 N일로 한정한다: 알림 기능을 처음 켠 날 몇 달 치 이벤트가 한꺼번에 나가는 일을 막는다
    events = [e for e in recent_events(days) if e.importance == 5 and e.id not in done]
    return sorted(events, key=lambda e: e.id or 0)


def mark_urgent_sent(event_ids: list[int]) -> None:
    """긴급 알림이 실제로 나간 이벤트를 기록한다. 같은 id 를 두 번 넘겨도 한 번만 기록된다.

    여기에 기록되지 않은 중요도 5 이벤트는 pending_urgent() 에 계속 나오므로, 발송에 성공한 것만 넘긴다.
    """
    now = datetime.now(KST).isoformat()
    with connect() as conn:
        for event_id in event_ids:
            stmt = upsert(urgent_alerts).values(event_id=event_id, sent_at=now)
            conn.execute(stmt.on_conflict_do_nothing(index_elements=["event_id"]))


# ---------- Report (C 생성, D 발송) ----------
def save_report(report: WeeklyReport, sent: bool = True) -> None:
    """리포트 저장. sent=True면 발송 시각을 찍는다.

    sent=False(dry-run·발송 실패)는 아직 발송되지 않은 주차만 덮어쓴다. 이미 발송된 리포트는
    실제로 나간 내용이 기록으로 남아야 하므로 건드리지 않는다.
    """
    sent_at = datetime.now(KST).isoformat() if sent else None
    stmt = upsert(reports).values(
        week=report.week, tldr=report.tldr, data=report.model_dump_json(), sent_at=sent_at
    )
    with connect() as conn:
        conn.execute(
            stmt.on_conflict_do_update(
                index_elements=["week"],
                set_={"tldr": stmt.excluded.tldr, "data": stmt.excluded.data, "sent_at": stmt.excluded.sent_at},
                # sent=False 면 "아직 발송 안 된 줄"일 때만 덮어쓴다 → 발송된 기록은 dry-run 이 못 건드린다
                where=None if sent else reports.c.sent_at.is_(None),
            )
        )


def report_channels_sent(week: str) -> set[str]:
    """이 주차 리포트가 이미 나간 채널. 예: {"slack"}"""
    with connect() as conn:
        rows = conn.execute(select(report_sends.c.channel).where(report_sends.c.week == week)).fetchall()
    return {r.channel for r in rows}


def mark_report_sent(week: str, channel: str) -> None:
    """리포트가 이 채널로 나갔음을 기록한다. 두 번 기록해도 한 번.

    channel 은 "slack"·"email"(채널 전체 완료) 또는 "slack#0"·"slack#1"(슬랙 메시지 하나).
    슬랙은 여러 메시지로 나가므로, 중간에 끊겼을 때 이어 보낼 수 있게 메시지 단위로도 남긴다.
    """
    stmt = upsert(report_sends).values(week=week, channel=channel, sent_at=datetime.now(KST).isoformat())
    with connect() as conn:
        conn.execute(stmt.on_conflict_do_nothing(index_elements=["week", "channel"]))


def report_status(limit: int = 8) -> list[dict]:
    """최근 리포트의 발송 현황, 최신 주차부터: [{"week", "sent_at", "channels"}].

    sent_at 이 None 이면 저장만 되고(dry-run·발송 실패) 아직 나가지 않은 리포트다.
    channels 는 끝까지 나간 채널 이름만 담는다 (슬랙 메시지 단위 기록 "slack#0" 등은 뺀다).
    """
    with connect() as conn:
        rows = conn.execute(
            select(reports.c.week, reports.c.sent_at).order_by(reports.c.week.desc()).limit(limit)
        ).fetchall()
        weeks = [r.week for r in rows]
        sends = conn.execute(
            select(report_sends.c.week, report_sends.c.channel).where(report_sends.c.week.in_(weeks))
        ).fetchall() if weeks else []
    channels: dict[str, list[str]] = {}
    for week, channel in sends:
        if "#" not in channel:
            channels.setdefault(week, []).append(channel)
    return [{"week": r.week, "sent_at": r.sent_at, "channels": sorted(channels.get(r.week, []))}
            for r in rows]


def last_report_tldr(week: str) -> str | None:
    """week **이전** 주차 중 가장 최근 리포트의 한 줄 요약. 없으면 None.

    C가 인사이트를 만들 때 "지난 리포트에서는 이렇게 말했다"를 프롬프트에 넣는 용도.
    """
    with connect() as conn:
        row = conn.execute(
            select(reports.c.tldr).where(reports.c.week < week).order_by(reports.c.week.desc()).limit(1)
        ).fetchone()
    return row.tldr if row else None


def get_report(week: str) -> WeeklyReport | None:
    """그 주차에 저장된 리포트. 없으면 None. (전주 대비 수치, 재발송 때 같은 내용 쓰기에 사용)"""
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
    """LLM 호출 한 번의 토큰 사용량을 기록한다. C의 llm_client 가 호출마다 부른다.

    예: repo.save_llm_call(resp.model, resp.usage.input_tokens, resp.usage.output_tokens)
    대시보드의 "LLM 사용량" 표가 이 기록의 합계다.
    """
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


# ---------- 발송 리허설 정리 (D 전용: app/infra/sample_data.py) ----------
def delete_events(ids: list[int]) -> None:
    """이벤트와 그 긴급 알림 기록을 지운다. 샘플 이벤트 정리용 — 실제 수집 이벤트에는 쓰지 않는다."""
    if not ids:
        return
    with connect() as conn:
        conn.execute(delete(urgent_alerts).where(urgent_alerts.c.event_id.in_(ids)))
        conn.execute(delete(change_events).where(change_events.c.id.in_(ids)))


def delete_report(week: str) -> None:
    """그 주차의 리포트와 발송 기록을 지운다. 리허설 주차 정리용."""
    with connect() as conn:
        conn.execute(delete(report_sends).where(report_sends.c.week == week))
        conn.execute(delete(reports).where(reports.c.week == week))
