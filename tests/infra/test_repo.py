"""[D] 저장소 함수 + A·B 파이프라인이 저장소에 붙는지 확인하는 계약 테스트 (수집기는 가짜)."""
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update
from typer.testing import CliRunner

from app.__main__ import cli
from app.core.models import Brand, ChangeEvent, ChangeType, RawItem, SourceType
from app.infra import KST
from app.infra.notify import slack
from app.infra.storage import db, repo
from app.media_monitor import pipeline

runner = CliRunner()


@pytest.fixture
def ops(database, monkeypatch):
    alerts: list[str] = []
    monkeypatch.setattr(slack, "send_ops_alert", alerts.append)
    return alerts


def _news(n: int, source: str = "naver_news", **kw) -> RawItem:
    url = f"https://news.example.com/{source}/{n}"
    return RawItem(brand=Brand.SPAO, source_type=SourceType.NEWS, source=source, external_id=url, url=url,
                   title=f"스파오 소식 {n}번 — {'가격 인하' if n % 2 else '신규 매장 오픈'} {n * 7919}", **kw)


def _post(n: int, likes: int, source: str = "instagram") -> RawItem:
    return RawItem(brand=Brand.SPAO, source_type=SourceType.SNS_POST, source=source, external_id=str(n),
                   url=f"https://sns.example.com/{source}/{n}", title=f"게시물 {n}", metrics={"likes": likes})


def _fake(source: str, batches: list[list[RawItem]]):
    """collect() 를 부를 때마다 batches 를 하나씩 돌려주는 가짜 수집기 클래스."""
    it = iter(batches)

    class Fake:
        def __init__(self, brand, config):
            self.source = source

        def collect(self):
            return next(it)

    return Fake


def _use_news(monkeypatch, naver, google):
    monkeypatch.setattr(pipeline, "NaverNewsCollector", _fake("naver_news", naver))
    monkeypatch.setattr(pipeline, "GoogleNewsCollector", _fake("google_news", google))


def _use_sns(monkeypatch, instagram, youtube):
    monkeypatch.setattr(pipeline, "InstagramCollector", _fake("instagram", instagram))
    monkeypatch.setattr(pipeline, "YoutubeCollector", _fake("youtube", youtube))


# ---------- B 파이프라인 ↔ 저장소 ----------
def test_news_pipeline_saves_new_articles_once(ops, monkeypatch):
    day1 = [_news(1), _news(2)]
    _use_news(monkeypatch, [day1, day1 + [_news(3)]], [[_news(4, "google_news")], []])

    assert len(pipeline.run_news(Brand.SPAO)) == 3
    events = pipeline.run_news(Brand.SPAO)  # 다음 날: 이미 본 기사는 다시 이벤트가 되지 않는다
    assert [e.change_type for e in events] == [ChangeType.NEWS] and events[0].id is not None
    assert len(repo.unclassified_events()) == 4


def test_sns_pipeline_detects_new_posts_and_spikes(ops, monkeypatch):
    history = [_post(n, likes=100) for n in range(5)]
    _use_sns(monkeypatch, [history, history + [_post(9, likes=900)]], [[], []])

    assert {e.change_type for e in pipeline.run_sns(Brand.SPAO)} == {ChangeType.SNS_NEW_POST}
    types = sorted(e.change_type.value for e in pipeline.run_sns(Brand.SPAO))
    assert types == ["sns_new_post", "sns_spike"]
    assert len(repo.recent_posts(Brand.SPAO, "instagram")) == 6


# ---------- 수집 0건 경고 ----------
def test_alert_when_one_sns_source_returns_nothing(ops, monkeypatch):
    _use_sns(monkeypatch, [[_post(1, 10)], []], [[_post(1, 10, "youtube")], [_post(2, 10, "youtube")]])
    assert runner.invoke(cli, ["collect", "--source", "sns", "--brand", "spao"]).exit_code == 0
    assert ops == []
    assert runner.invoke(cli, ["collect", "--source", "sns", "--brand", "spao"]).exit_code == 0
    assert len(ops) == 1 and "instagram" in ops[0]


def test_alert_when_nothing_collected_at_all(ops, monkeypatch):
    _use_sns(monkeypatch, [[]], [[]])
    runner.invoke(cli, ["collect", "--source", "sns", "--brand", "spao"])
    assert len(ops) == 1 and "수집 0건 sns/spao" in ops[0]


def test_news_alert_only_when_stale(ops, monkeypatch):
    old = datetime.now(KST) - timedelta(days=5)
    _use_news(monkeypatch, [[_news(1)], [_news(1)], []], [[], [], []])
    runner.invoke(cli, ["collect", "--source", "news", "--brand", "spao"])
    runner.invoke(cli, ["collect", "--source", "news", "--brand", "spao"])  # 신규 0건이지만 어제 수집됨
    assert ops == []
    with db.connect() as conn:
        conn.execute(update(db.raw_items).values(collected_at=old.isoformat()))
    runner.invoke(cli, ["collect", "--source", "news", "--brand", "spao"])
    assert len(ops) == 1 and "신규 기사 없음" in ops[0]


# ---------- 스냅샷 보관 ----------
def test_cleanup_removes_only_old_snapshots(ops):
    now = datetime.now(KST)
    repo.save_items([_news(1, collected_at=now - timedelta(weeks=9)), _news(2, collected_at=now)])
    result = runner.invoke(cli, ["cleanup"])
    assert "1건 삭제" in result.output
    assert repo.snapshot_counts(Brand.SPAO, [SourceType.NEWS]) == {"naver_news": 1}
    assert len(repo.latest_items(Brand.SPAO, "naver_news")) == 2  # 최신 상태는 남는다


# ---------- LLM 사용량 ----------
def test_llm_usage_sums_by_model(ops):
    repo.save_llm_call("model-a", 100, 20)
    repo.save_llm_call("model-a", 50, 10)
    repo.save_llm_call("model-b", 7, 3)
    y, w, _ = datetime.now(KST).isocalendar()
    assert repo.llm_usage(f"{y}-W{w:02d}") == {
        "model-a": {"calls": 2, "input_tokens": 150, "output_tokens": 30},
        "model-b": {"calls": 1, "input_tokens": 7, "output_tokens": 3},
    }


# ---------- 시각은 KST 기준 ----------
def _event(detected_at: datetime | None = None) -> ChangeEvent:
    kw = {"detected_at": detected_at} if detected_at else {}
    return ChangeEvent(brand=Brand.SPAO, change_type=ChangeType.NEWS, title="t", url="https://example.com/n", **kw)


def test_default_timestamps_are_kst():
    assert _event().detected_at.utcoffset() == timedelta(hours=9)
    assert _news(1).collected_at.utcoffset() == timedelta(hours=9)


def test_week_boundary_follows_kst_not_utc(ops):
    """UTC 러너가 일요일 21:00 UTC 에 찍은 이벤트는 KST 로 월요일 06:00 — 새 주에 속해야 한다."""
    repo.save_events([_event(datetime(2026, 11, 22, 21, tzinfo=UTC)),
                      _event(datetime(2026, 11, 22, 14, tzinfo=UTC))])  # KST 일요일 23:00
    assert len(repo.events_of_week("2026-W48")) == 1
    assert len(repo.events_of_week("2026-W47")) == 1


def test_naive_timestamp_is_read_as_kst(ops):
    repo.save_events([_event(datetime.fromisoformat("2026-11-23T00:30:00"))])
    assert len(repo.events_of_week("2026-W48")) == 1


# ---------- DB 접속 설정 ----------
def test_database_url_accepts_plain_postgres_scheme(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:p%40ss@db.example.com:5432/postgres")
    url = db.database_url()
    assert url.drivername == "postgresql+psycopg" and url.password == "p@ss"
    assert db.describe() == "postgresql: db.example.com"


def test_database_url_defaults_to_sqlite_file(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "")  # GitHub Actions 는 없는 secret 을 빈 문자열로 준다
    assert db.database_url().get_backend_name() == "sqlite"
