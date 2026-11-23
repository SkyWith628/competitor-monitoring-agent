"""[B] SNS 파이프라인 테스트 — 수집기 하나가 실패·미구현이어도 나머지는 저장되는지.

(최근 7일 기준 테스트는 #20의 test_pipeline.py. 같은 파일명이면 두 PR이 머지 때 충돌해서 파일을 나눔)
"""
import pytest

from app.core.models import Brand, ChangeType, RawItem, SourceType
from app.media_monitor import pipeline


def _post(source: str, pid: str) -> RawItem:
    return RawItem(brand=Brand.SPAO, source_type=SourceType.SNS_POST, source=source,
                   external_id=pid, url=f"https://example.com/{pid}", title=f"게시물 {pid}")


class _Fake:
    """collect() 결과(또는 예외)를 정해 둘 수 있는 가짜 수집기."""
    def __init__(self, source, result):
        self.source, self.result = source, result

    def __call__(self, brand, cfg):
        return self

    def collect(self):
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class _FakeRepo:
    def __init__(self):
        self.items, self.events = [], []

    def recent_posts(self, brand, source, limit=30):
        return []

    def exists(self, item):
        return False

    def save_items(self, items):
        self.items += items

    def save_events(self, events):
        self.events += events


@pytest.fixture
def fake_repo(monkeypatch):
    r = _FakeRepo()
    monkeypatch.setattr(pipeline, "repo", r)
    monkeypatch.setattr(pipeline, "brands_config", lambda: {"spao": {"sns": {}}})
    return r


def _use(monkeypatch, instagram, youtube):
    monkeypatch.setattr(pipeline, "InstagramCollector", _Fake("instagram", instagram))
    monkeypatch.setattr(pipeline, "YoutubeCollector", _Fake("youtube", youtube))


def test_instagram_failure_does_not_stop_youtube(monkeypatch, fake_repo):
    # 인스타 토큰 만료 같은 실패가 나도 유튜브 결과는 저장하고, 실패는 마지막에 예외로 알린다
    _use(monkeypatch, RuntimeError("Instagram API 오류 190: token expired"), [_post("youtube", "v1")])
    with pytest.raises(RuntimeError, match="instagram") as e:
        pipeline.run_sns(Brand.SPAO)
    assert "youtube" not in str(e.value)
    assert [i.external_id for i in fake_repo.items] == ["v1"]
    assert [ev.change_type for ev in fake_repo.events] == [ChangeType.SNS_NEW_POST]


def test_not_implemented_collector_is_skipped(monkeypatch, fake_repo):
    _use(monkeypatch, NotImplementedError(), [_post("youtube", "v1")])
    events = pipeline.run_sns(Brand.SPAO)
    assert len(events) == 1


def test_all_collectors_not_implemented(monkeypatch, fake_repo):
    _use(monkeypatch, NotImplementedError(), NotImplementedError())
    with pytest.raises(NotImplementedError):
        pipeline.run_sns(Brand.SPAO)
