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


def test_title_with_brand_becomes_representative(monkeypatch):
    # 같은 소식이면 '[식음료 소식] …' 같은 묶음 기사보다 제목에 브랜드명이 있는 기사가 이벤트 제목이 된다
    body = "농심이 새우깡 출시 55주년을 맞아 무신사 스탠다드와 손잡고 커스텀 티셔츠 서비스를 선보인다."
    roundup = _news("https://c.example.com/3", "[식음료 소식] KT&Gㆍ풀무원ㆍ농심").model_copy(update={"body": body})
    titled = _news("https://d.example.com/4", "농심, 무신사 스탠다드와 새우깡 티셔츠").model_copy(update={"body": body})

    class Collector:
        def __init__(self, brand, cfg):
            pass

        def collect(self):
            return [roundup, titled]

    monkeypatch.setattr(pipeline, "brands_config",
                        lambda: {"musinsa_standard": {"news": {"keywords": ["무신사 스탠다드"]}}})
    monkeypatch.setattr(pipeline, "NaverNewsCollector", Collector)
    monkeypatch.setattr(pipeline, "GoogleNewsCollector", lambda brand, cfg: type("E", (), {"collect": lambda s: []})())
    monkeypatch.setattr(pipeline.repo, "exists", lambda item: False)
    monkeypatch.setattr(pipeline.repo, "save_items", lambda items: None)
    monkeypatch.setattr(pipeline.repo, "save_events", lambda events: None)

    assert [e.title for e in pipeline.run_news(Brand.MUSINSA_STANDARD)] == ["농심, 무신사 스탠다드와 새우깡 티셔츠"]
