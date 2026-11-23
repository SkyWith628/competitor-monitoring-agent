"""[B] 뉴스 파이프라인 — 날이 바뀌어도 같은 소식이 다시 이벤트가 되지 않는지."""
from app.core.models import Brand, RawItem, SourceType
from app.media_monitor import pipeline


def _news(url: str, title: str) -> RawItem:
    return RawItem(brand=Brand.MUSINSA_STANDARD, source_type=SourceType.NEWS, source="google_news",
                   external_id=url, url=url, title=title)


SAVED = _news("https://a.example.com/1", "무신사 스탠다드, 제주 첫 매장")  # 어제 저장·알림한 기사
FOLLOW_UP = _news("https://b.example.com/2", "무신사 스탠다드, 제주 첫 매장 연다…전국 45번째")  # 오늘 새 기사


def test_follow_up_article_of_saved_story_is_not_new_event(monkeypatch):
    class Collector:
        def __init__(self, brand, cfg):
            pass

        def collect(self):
            return [FOLLOW_UP, SAVED]  # 새 기사가 목록 앞에 와도

    saved_events = []
    monkeypatch.setattr(pipeline, "brands_config",
                        lambda: {"musinsa_standard": {"news": {"keywords": ["무신사 스탠다드"]}}})
    monkeypatch.setattr(pipeline, "NaverNewsCollector", Collector)
    monkeypatch.setattr(pipeline, "GoogleNewsCollector", lambda brand, cfg: type("E", (), {"collect": lambda s: []})())
    monkeypatch.setattr(pipeline.repo, "exists", lambda item: item.url == SAVED.url)
    monkeypatch.setattr(pipeline.repo, "save_items", lambda items: None)
    monkeypatch.setattr(pipeline.repo, "save_events", lambda events: saved_events.extend(events))

    assert pipeline.run_news(Brand.MUSINSA_STANDARD) == []  # 저장된 기사가 대표 → 새 소식 없음
    assert saved_events == []
