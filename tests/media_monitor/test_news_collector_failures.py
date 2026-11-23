"""[B] 뉴스 파이프라인 — 수집기 하나가 실패·미구현이어도 나머지는 저장되는지.

(파일 이름을 test_pipeline.py 로 하면 #20, #6 의 테스트 파일과 머지 때 충돌해서 따로 둠)
"""
import pytest

from app.core.models import Brand, RawItem, SourceType
from app.media_monitor import pipeline


def _news(source: str, n: int) -> RawItem:
    return RawItem(brand=Brand.SPAO, source_type=SourceType.NEWS, source=source, external_id=f"{source}{n}",
                   url=f"https://{source}.example.com/{n}", title=f"스파오 소식 {source} {n}")


def _collector(source: str, result):
    class Fake:
        def __init__(self, brand, cfg):
            pass

        def collect(self):
            if isinstance(result, BaseException):
                raise result
            return result
    Fake.source = source
    return Fake


@pytest.fixture
def saved(monkeypatch):
    s = {"items": [], "events": []}
    monkeypatch.setattr(pipeline, "brands_config", lambda: {"spao": {"news": {"keywords": ["스파오"]}}})
    monkeypatch.setattr(pipeline.repo, "exists", lambda item: False)
    monkeypatch.setattr(pipeline.repo, "save_items", lambda items: s["items"].extend(items))
    monkeypatch.setattr(pipeline.repo, "save_events", lambda events: s["events"].extend(events))
    return s


def _use(monkeypatch, naver, google):
    monkeypatch.setattr(pipeline, "NaverNewsCollector", _collector("naver_news", naver))
    monkeypatch.setattr(pipeline, "GoogleNewsCollector", _collector("google_news", google))


def test_naver_failure_does_not_stop_google(monkeypatch, saved):
    # 네이버 키 만료 같은 실패가 나도 구글 결과는 저장하고, 실패는 마지막에 예외로 알린다
    _use(monkeypatch, RuntimeError("401 Unauthorized"), [_news("google_news", 1)])
    with pytest.raises(RuntimeError, match="naver_news") as e:
        pipeline.run_news(Brand.SPAO)
    assert "google_news:" not in str(e.value)
    assert [i.source for i in saved["items"]] == ["google_news"]
    assert len(saved["events"]) == 1


def test_not_implemented_collector_is_skipped(monkeypatch, saved):
    _use(monkeypatch, [_news("naver_news", 1)], NotImplementedError())
    assert len(pipeline.run_news(Brand.SPAO)) == 1


def test_all_collectors_not_implemented(monkeypatch, saved):
    _use(monkeypatch, NotImplementedError(), NotImplementedError())
    with pytest.raises(NotImplementedError):
        pipeline.run_news(Brand.SPAO)
