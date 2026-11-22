"""[D] 저장소 함수 테스트 — A·B·C가 기대하는 동작을 고정한다."""
from app.core.models import Brand, Category, ChangeEvent, ChangeType, RawItem, SourceType, WeeklyReport
from app.infra.storage import db, repo


def _item(ext: str = "p1", price: int = 39900, **kw) -> RawItem:
    base = {"brand": Brand.SPAO, "source_type": SourceType.SITE_PRODUCT, "source": "spao.com",
            "external_id": ext, "url": f"https://spao.com/{ext}", "title": "티셔츠", "price": price}
    return RawItem(**(base | kw))


def _event(day: int, **kw) -> ChangeEvent:
    return ChangeEvent(brand=Brand.SPAO, change_type=ChangeType.PRICE_DOWN, title="할인",
                       url="https://spao.com/p1", detected_at=f"2026-11-{day:02d}T09:00:00", **kw)


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
