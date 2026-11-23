"""[B] SNS 파이프라인 테스트 — 수집기·저장소를 가짜로 바꿔 '새 게시물' 이벤트 기간만 확인."""
from datetime import datetime, timedelta

from app.core.models import Brand, ChangeType, RawItem, SourceType
from app.media_monitor import pipeline


def _post(pid: str, days_ago: float | None) -> RawItem:
    published = None if days_ago is None else datetime.now() - timedelta(days=days_ago)
    return RawItem(brand=Brand.SPAO, source_type=SourceType.SNS_POST, source="youtube", external_id=pid,
                   url=f"https://youtu.be/{pid}", title=pid, published_at=published)


def test_new_post_events_only_within_recent_days(monkeypatch):
    posts = [_post("new", 1), _post("old", 400), _post("unknown", None)]

    class FakeCollector:
        source = "youtube"

        def __init__(self, brand, cfg):
            pass

        def collect(self):
            return posts

    saved = []
    monkeypatch.setattr(pipeline, "brands_config", lambda: {"spao": {"sns": {}}})
    monkeypatch.setattr(pipeline, "InstagramCollector", FakeCollector)
    monkeypatch.setattr(pipeline, "YoutubeCollector", FakeCollector)
    monkeypatch.setattr(pipeline.repo, "recent_posts", lambda *a, **k: [])
    monkeypatch.setattr(pipeline.repo, "exists", lambda item: False)  # 처음 실행: 전부 처음 보는 글
    monkeypatch.setattr(pipeline.repo, "save_items", lambda items: saved.extend(items))
    monkeypatch.setattr(pipeline.repo, "save_events", lambda events: None)

    events = pipeline.run_sns(Brand.SPAO)
    titles = {e.title for e in events if e.change_type == ChangeType.SNS_NEW_POST}
    assert titles == {"new", "unknown"}  # 400일 전 글은 '새 게시물' 아님
    assert {i.title for i in saved} >= {"old"}  # 그래도 저장은 함 (급증 감지 비교용)
