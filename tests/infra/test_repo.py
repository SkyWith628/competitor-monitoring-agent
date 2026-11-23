"""[D] 저장소 함수 테스트 — A·B·C가 기대하는 동작을 고정한다."""
from datetime import UTC, datetime, timedelta

import pytest

from app.core.models import Brand, Category, ChangeEvent, ChangeType, RawItem, SourceType, WeeklyReport
from app.infra.storage import db, repo


def _now() -> datetime:
    """수집기가 찍는 datetime.now()와 같은 로컬 naive 시각."""
    return datetime.now(UTC).astimezone().replace(tzinfo=None)


def _item(ext: str = "p1", price: int = 39900, **kw) -> RawItem:
    base = {"brand": Brand.SPAO, "source_type": SourceType.SITE_PRODUCT, "source": "spao.com",
            "external_id": ext, "url": f"https://spao.com/{ext}", "title": "티셔츠", "price": price}
    return RawItem(**(base | kw))


def _event(day: int, **kw) -> ChangeEvent:
    base = {"brand": Brand.SPAO, "change_type": ChangeType.PRICE_DOWN, "title": "할인",
            "url": "https://spao.com/p1", "detected_at": f"2026-11-{day:02d}T09:00:00"}
    return ChangeEvent(**(base | kw))


def _report(week: str, tldr: str) -> WeeklyReport:
    return WeeklyReport(week=week, tldr=tldr, top_events=[], brand_summaries=[], insights=[], actions=[])


def test_tables_exist_without_init_db(tmp_db):
    assert repo.latest_items(Brand.SPAO, "spao.com") == {}


def test_save_items_keeps_latest_and_every_snapshot(tmp_db):
    repo.save_items([_item(price=39900)])
    repo.save_items([_item(price=29900)])
    assert repo.latest_items(Brand.SPAO, "spao.com")["p1"].price == 29900
    assert repo.exists(_item()) and not repo.exists(_item(ext="p2"))
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 2


def test_recent_posts_only_sns_newest_first(tmp_db):
    posts = [_item(ext=f"s{d}", source="instagram", source_type=SourceType.SNS_POST,
                   collected_at=f"2026-11-0{d}T00:00:00") for d in (1, 3, 2)]
    repo.save_items([*posts, _item(source="instagram")])
    assert [p.external_id for p in repo.recent_posts(Brand.SPAO, "instagram", limit=2)] == ["s3", "s2"]


def test_events_get_ids_and_classification_roundtrip(tmp_db):
    events = [_event(17), _event(18)]
    repo.save_events(events)
    assert [e.id for e in events] == [1, 2]
    assert [e.id for e in repo.unclassified_events()] == [1, 2]

    events[0].category, events[0].importance = Category.PRICING, 4
    repo.update_events([events[0]])
    assert [e.id for e in repo.unclassified_events()] == [2]


def test_events_of_week_is_monday_to_sunday(tmp_db):
    repo.save_events([_event(15), _event(16), _event(22), _event(23)])  # 일 / 월 / 일 / 다음 월
    assert [e.detected_at.day for e in repo.events_of_week("2026-W47")] == [16, 22]


def test_last_report_tldr_is_previous_week(tmp_db):
    repo.save_report(_report("2026-W45", "45주"))
    repo.save_report(_report("2026-W46", "46주"))
    assert repo.last_report_tldr("2026-W47") == "46주"
    assert repo.last_report_tldr("2026-W45") is None


def test_dry_run_save_does_not_mark_sent_or_clear_it(tmp_db):
    def sent_at():
        with db.connect() as conn:
            return conn.execute("SELECT sent_at FROM reports WHERE week='2026-W47'").fetchone()[0]

    repo.save_report(_report("2026-W47", "초안"), sent=False)
    assert sent_at() is None
    repo.save_report(_report("2026-W47", "발송본"))
    first = sent_at()
    assert first is not None
    repo.save_report(_report("2026-W47", "재실행"), sent=False)
    assert sent_at() == first


def test_database_url_selects_backend(tmp_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")
    assert db.database_url().startswith("sqlite:///") and not db.is_postgres()
    for raw in ("postgres://u:p@h:5432/d", "postgresql://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"):
        monkeypatch.setenv("DATABASE_URL", raw)
        assert db.database_url() == "postgresql+psycopg://u:p@h:5432/d" and db.is_postgres()


def test_init_db_creates_tables(tmp_db):
    db.init_db()
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 0


def test_count_items_since_filters_brand_type_and_time(tmp_db):
    now = _now()
    old, new = now - timedelta(days=2), now - timedelta(hours=1)
    repo.save_items([
        _item(ext="old", collected_at=old),
        _item(ext="new", collected_at=new),
        _item(ext="page", source_type=SourceType.SITE_PAGE, collected_at=new),
        _item(ext="news", source_type=SourceType.NEWS, source="naver_news", collected_at=new),
        _item(ext="other", brand=Brand.UNIQLO, collected_at=new),
    ])
    since = now - timedelta(days=1)
    assert repo.count_items_since(Brand.SPAO, [SourceType.SITE_PRODUCT], since) == 1
    assert repo.count_items_since(Brand.SPAO, [SourceType.SITE_PRODUCT, SourceType.SITE_PAGE], since) == 2
    assert repo.count_items_since(Brand.SPAO, [SourceType.SITE_PRODUCT], now - timedelta(days=3)) == 2
    assert repo.count_items_since(Brand.SPAO, [SourceType.SITE_PRODUCT], new) == 1  # 경계 포함(>=)
    assert repo.count_items_since(Brand.SPAO, [SourceType.SNS_POST], since) == 0
    assert repo.count_items_since(Brand.SPAO, [], since) == 0
    assert repo.count_items_since(Brand.SPAO, [SourceType.SITE_PRODUCT], since.astimezone()) == 1  # tz 있어도 됨


def test_item_counts_by_brand_and_source_type(tmp_db):
    assert repo.item_counts() == {}
    repo.save_items([
        _item(ext="a"), _item(ext="b"), _item(ext="a"),  # 같은 키는 한 건
        _item(ext="n", source_type=SourceType.NEWS, source="naver_news"),
        _item(ext="u", brand=Brand.UNIQLO),
    ])
    assert repo.item_counts() == {"spao": {"site_product": 2, "news": 1}, "uniqlo": {"site_product": 1}}


def test_prune_snapshots_deletes_only_old_ones_and_keeps_latest_items(tmp_db):
    now = _now()
    repo.save_items([_item(collected_at=now - timedelta(weeks=9)),
                     _item(ext="p2", collected_at=now - timedelta(weeks=7)), _item(ext="p3", collected_at=now)])
    assert repo.prune_snapshots() == 1
    assert repo.prune_snapshots() == 0
    assert repo.prune_snapshots(weeks=1) == 1
    with db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
    assert set(repo.latest_items(Brand.SPAO, "spao.com")) == {"p1", "p2", "p3"}


def test_llm_usage_aggregates_per_model(tmp_db):
    assert repo.llm_usage() == []
    repo.save_llm_call("claude-haiku", 100, 10)
    repo.save_llm_call("claude-haiku", 200, 20)
    repo.save_llm_call("claude-sonnet", 1000, 300)
    assert repo.llm_usage() == [
        {"model": "claude-haiku", "calls": 2, "input_tokens": 300, "output_tokens": 30},
        {"model": "claude-sonnet", "calls": 1, "input_tokens": 1000, "output_tokens": 300},
    ]
    with db.connect() as conn:  # 기간 밖 호출은 빠진다
        conn.execute("UPDATE llm_calls SET called_at = :t WHERE model = 'claude-sonnet'",
                     {"t": (_now() - timedelta(days=40)).isoformat()})
    assert [u["model"] for u in repo.llm_usage(days=30)] == ["claude-haiku"]
    assert len(repo.llm_usage(days=60)) == 2


def test_recent_events_newest_first_within_days(tmp_db):
    now = _now()
    events = [_event(1, detected_at=now - timedelta(days=d), title=f"d{d}") for d in (40, 3, 10, 0)]
    repo.save_events(events)
    assert [e.title for e in repo.recent_events()] == ["d0", "d3", "d10"]
    assert [e.title for e in repo.recent_events(days=5)] == ["d0", "d3"]
    assert repo.recent_events()[0].id == events[3].id


def test_get_and_list_reports(tmp_db):
    assert repo.get_report("2026-W47") is None and repo.list_reports() == []
    for week in ("2026-W46", "2026-W45", "2026-W47"):
        repo.save_report(_report(week, f"{week} 요약"), sent=False)
    repo.save_report(_report("2026-W46", "수정본"))
    assert repo.get_report("2026-W46") == _report("2026-W46", "수정본")
    assert [r.week for r in repo.list_reports()] == ["2026-W47", "2026-W46", "2026-W45"]
    assert [r.week for r in repo.list_reports(limit=2)] == ["2026-W47", "2026-W46"]


def test_dry_run_save_never_overwrites_a_sent_report(tmp_db):
    repo.save_report(_report("2026-W47", "발송본"))
    repo.save_report(_report("2026-W47", "나중의 dry-run"), sent=False)
    assert repo.get_report("2026-W47").tldr == "발송본"
    assert repo.last_report_tldr("2026-W48") == "발송본"  # 다음 주 리포트는 실제로 나간 내용을 참고한다
    repo.save_report(_report("2026-W47", "재발송"))  # --force 재발송은 덮어쓴다
    assert repo.get_report("2026-W47").tldr == "재발송"


def test_draft_can_be_overwritten_until_sent(tmp_db):
    repo.save_report(_report("2026-W47", "초안 1"), sent=False)
    repo.save_report(_report("2026-W47", "초안 2"), sent=False)
    assert repo.get_report("2026-W47").tldr == "초안 2"


def test_report_channels_sent_per_week(tmp_db):
    assert repo.report_channels_sent("2026-W47") == set()
    repo.mark_report_sent("2026-W47", "slack")
    repo.mark_report_sent("2026-W47", "slack")  # 두 번 기록해도 한 번
    repo.mark_report_sent("2026-W46", "email")
    assert repo.report_channels_sent("2026-W47") == {"slack"}


def test_pending_urgent_excludes_sent_and_lower_importance(tmp_db):
    now = _now()
    events = [_event(17, detected_at=now, importance=5), _event(17, detected_at=now, importance=4),
              _event(17, detected_at=now, importance=5), _event(17, detected_at=now - timedelta(days=30), importance=5)]
    repo.save_events(events)
    assert [e.id for e in repo.pending_urgent()] == [1, 3]  # 4번은 너무 오래됨
    repo.mark_urgent_sent([1])
    repo.mark_urgent_sent([1])
    assert [e.id for e in repo.pending_urgent()] == [3]


def test_events_stamped_on_monday_morning_kst_belong_to_the_new_week(tmp_db):
    """월요일 06:00 KST 수집분(= 일요일 21:00 UTC)이 전주 리포트로 들어가면 안 된다."""
    from zoneinfo import ZoneInfo

    monday_6am_kst = datetime(2026, 11, 23, 6, 0, tzinfo=ZoneInfo("Asia/Seoul"))
    stamped = monday_6am_kst.astimezone().replace(tzinfo=None)  # 모델이 datetime.now() 로 찍는 값
    repo.save_events([_event(1, detected_at=stamped)])
    assert repo.events_of_week("2026-W47") == []
    assert len(repo.events_of_week("2026-W48")) == 1


def test_silent_sources_finds_a_source_that_stopped_producing(tmp_db):
    now = _now()
    since = now - timedelta(minutes=5)
    sns = {"source_type": SourceType.SNS_POST}
    repo.save_items([_item(ext="y1", source="youtube", collected_at=now, **sns),
                     _item(ext="y0", source="youtube", collected_at=now - timedelta(days=2), **sns),
                     _item(ext="i1", source="instagram", collected_at=now - timedelta(days=1), **sns),
                     _item(ext="p1", source="spao.com", collected_at=now - timedelta(days=1))])
    assert repo.silent_sources(Brand.SPAO, [SourceType.SNS_POST], since) == ["instagram"]
    assert repo.silent_sources(Brand.SPAO, [SourceType.SITE_PRODUCT], since) == ["spao.com"]
    assert repo.silent_sources(Brand.UNIQLO, [SourceType.SNS_POST], since) == []  # 수집한 적 없는 소스는 대상 아님
    assert repo.silent_sources(Brand.SPAO, [], since) == []


def test_source_freshness_lists_each_source_with_latest_time(tmp_db):
    assert repo.source_freshness() == []
    old, new = datetime(2026, 11, 20, 6, 0), datetime(2026, 11, 22, 6, 0)
    sns = {"source_type": SourceType.SNS_POST}
    repo.save_items([_item(ext="p1", collected_at=old), _item(ext="p2", collected_at=new),
                     _item(ext="y1", source="youtube", collected_at=old, **sns),
                     _item(ext="u1", brand=Brand.UNIQLO, source="uniqlo.com", collected_at=new)])
    assert repo.source_freshness() == [
        {"brand": "spao", "source": "spao.com", "source_type": "site_product", "items": 2,
         "last_collected_at": "2026-11-22T06:00:00"},
        {"brand": "spao", "source": "youtube", "source_type": "sns_post", "items": 1,
         "last_collected_at": "2026-11-20T06:00:00"},
        {"brand": "uniqlo", "source": "uniqlo.com", "source_type": "site_product", "items": 1,
         "last_collected_at": "2026-11-22T06:00:00"},
    ]


def test_unclassified_count_matches_unclassified_events(tmp_db):
    assert repo.unclassified_count() == 0
    events = [_event(17), _event(18), _event(19)]
    repo.save_events(events)
    assert repo.unclassified_count() == 3
    events[0].category = Category.PRICING
    repo.update_events([events[0]])
    assert repo.unclassified_count() == len(repo.unclassified_events()) == 2


def test_report_status_shows_sent_state_and_whole_channels_only(tmp_db):
    assert repo.report_status() == []
    repo.save_report(_report("2026-W46", "발송 완료"))
    repo.mark_report_sent("2026-W46", "slack#0")
    repo.mark_report_sent("2026-W46", "slack")
    repo.mark_report_sent("2026-W46", "email")
    repo.save_report(_report("2026-W47", "초안"), sent=False)
    repo.mark_report_sent("2026-W47", "slack#0")  # 본 메시지만 나가고 멈춘 상태
    status = repo.report_status()
    assert [s["week"] for s in status] == ["2026-W47", "2026-W46"]
    assert status[0]["sent_at"] is None and status[0]["channels"] == []
    assert status[1]["sent_at"] is not None and status[1]["channels"] == ["email", "slack"]
    assert len(repo.report_status(limit=1)) == 1


def test_indexes_are_added_to_a_database_created_before_them(tmp_db):
    """인덱스가 없던 시절에 만들어진 DB 파일에도 다음 연결 때 인덱스가 생긴다."""
    from sqlalchemy import inspect

    def index_names():
        insp = inspect(db.engine())
        return {i["name"] for t in ("raw_items", "snapshots", "change_events", "llm_calls")
                for i in insp.get_indexes(t)}

    expected = {i.name for i in db.INDEXES}
    assert expected <= index_names()
    with db.connect() as conn:
        for name in expected:
            conn.execute(f"DROP INDEX {name}")
    assert not expected & index_names()
    db.init_db()
    assert expected <= index_names()


def test_backend_and_ping(tmp_db, monkeypatch):
    import os

    assert db.backend() == ("postgresql" if os.getenv("TEST_DATABASE_URL") else "sqlite")
    db.ping()


def test_postgres_tables_have_row_level_security(tmp_db):
    """Supabase 는 public 테이블을 REST API 로 내보낸다 — RLS 가 꺼진 테이블이 하나도 없어야 한다."""
    if not db.is_postgres():
        pytest.skip("PostgreSQL 전용 (TEST_DATABASE_URL)")
    with db.connect() as conn:
        rows = conn.execute("SELECT relname FROM pg_class WHERE relkind = 'r' AND NOT relrowsecurity "
                            "AND relnamespace = current_schema()::regnamespace").fetchall()
    assert [r[0] for r in rows if r[0] in db.metadata.tables] == []
