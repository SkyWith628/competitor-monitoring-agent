"""[A] 쇼핑인사이트 추이 수집 테스트 — 가짜 응답만 사용, 실 API 호출 없음."""
import json
from datetime import date

import pytest

from app.core.config import brands_config
from app.core.models import Brand, ChangeType
from app.site_monitor import pipeline
from app.site_monitor.trend import (
    ShoppingInsightTrend,
    brand_keywords,
    fill_days,
    save,
    save_spike_events,
    spike,
    spike_events,
)


def test_brands_yaml_has_keyword_for_all_five():
    kw = brand_keywords()
    assert set(kw) == set(Brand)
    assert kw[Brand.SPAO] == "스파오" and kw[Brand.WHOAU] == "후아유"


def test_brands_yaml_no_longer_routes_to_naver_shopping():
    for b in Brand:
        assert brands_config()[b.value]["site"].get("via") is None


def test_fill_days_zero_fills_missing():
    pts = [{"period": "2026-11-01", "ratio": 10}, {"period": "2026-11-03", "ratio": 30.5}]
    assert fill_days(pts, date(2026, 11, 1), date(2026, 11, 3)) == [
        ("2026-11-01", 10.0), ("2026-11-02", 0.0), ("2026-11-03", 30.5)]


def _series(values):
    return [(f"2026-11-{i + 1:02d}", float(v)) for i, v in enumerate(values)]


def test_spike_rules():
    assert spike(_series([10] * 7 + [16])) == {"period": "2026-11-08", "ratio": 16.0, "baseline": 10.0, "x": 1.6}
    assert spike(_series([10] * 7 + [14])) is None   # 1.5배 미만
    assert spike(_series([2] * 7 + [4])) is None      # 값이 너무 작음 (잡음)
    assert spike(_series([0] * 7 + [50])) is None     # 기준이 0 → 판단 안 함
    assert spike(_series([10] * 5 + [30])) is None    # 기간 부족


def test_collect_one_request_for_all_groups():
    kws = {Brand.SPAO: "스파오", Brand.WHOAU: "후아유"}
    c = ShoppingInsightTrend(kws)
    bodies = []

    def fake(body):
        bodies.append(body)
        return {"results": [
            {"title": "spao", "keyword": ["스파오"], "data": [{"period": "2026-11-01", "ratio": 100}]},
            {"title": "whoau", "keyword": ["후아유"], "data": []},
        ]}

    c._request = fake
    r = c.collect(days=2, end=date(2026, 11, 2))
    assert len(bodies) == 1
    assert bodies[0] == {"startDate": "2026-11-01", "endDate": "2026-11-02", "timeUnit": "date",
                         "category": "50000000",
                         "keyword": [{"name": "spao", "param": ["스파오"]}, {"name": "whoau", "param": ["후아유"]}]}
    assert r["brands"]["spao"]["series"] == [{"period": "2026-11-01", "ratio": 100.0},
                                             {"period": "2026-11-02", "ratio": 0.0}]
    assert r["brands"]["whoau"]["series"][0]["ratio"] == 0.0
    assert r["brands"]["spao"]["spike"] is None


def test_group_limit_and_empty():
    with pytest.raises(ValueError):
        ShoppingInsightTrend({})
    six = {b: b.value for b in Brand} | {"extra": "x"}
    with pytest.raises(ValueError):
        ShoppingInsightTrend(six)


def test_missing_keys_raise(monkeypatch):
    monkeypatch.delenv("NCP_INSIGHT_KEY_ID", raising=False)
    monkeypatch.delenv("NCP_INSIGHT_KEY", raising=False)
    with pytest.raises(RuntimeError, match="NCP_INSIGHT_KEY_ID"):
        ShoppingInsightTrend({Brand.SPAO: "스파오"})._request({})


def test_save_writes_json(tmp_path):
    path = save({"end": "2026-11-22", "brands": {"spao": {"keyword": "스파오"}}}, tmp_path)
    assert path.name == "shopping_insight-2026-11-22.json"
    assert json.loads(path.read_text(encoding="utf-8"))["brands"]["spao"]["keyword"] == "스파오"


def _result(spikes: dict[str, dict | None]) -> dict:
    return {"source": "naver_shopping_insight", "category": "50000000", "end": "2026-11-22",
            "brands": {name: {"keyword": name.upper(), "series": [], "spike": s} for name, s in spikes.items()}}


SPIKE = {"period": "2026-11-22", "ratio": 30.0, "baseline": 10.0, "x": 3.0}


def test_spike_events_only_for_spiking_brands():
    events = spike_events(_result({"spao": SPIKE, "whoau": None}))
    assert len(events) == 1
    e = events[0]
    assert e.brand == Brand.SPAO and e.change_type == ChangeType.TREND_SPIKE
    assert "2026-11-22" in e.title and "3.0배" in e.title
    assert e.url.startswith("https://datalab.naver.com/")
    assert e.detail == {"source": "naver_shopping_insight", "keyword": "SPAO", "category": "50000000",
                        "period": "2026-11-22", "ratio": 30.0, "baseline": 10.0, "x": 3.0}


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    from app.infra.storage import db
    from app.infra.timeutil import use_korean_time

    use_korean_time()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("REQUIRE_DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "monitor.db")


def test_save_spike_events_dedupes_same_day(tmp_db):
    from app.infra.storage import repo

    first = save_spike_events(_result({"spao": SPIKE, "whoau": None}))
    assert len(first) == 1 and first[0].id is not None
    # 같은 날 다시 실행: 이미 저장된 급증은 건너뛰고, 새 브랜드 급증만 저장
    again = save_spike_events(_result({"spao": SPIKE, "whoau": SPIKE}))
    assert [e.brand for e in again] == [Brand.WHOAU]
    # 다음 날 급증은 새 이벤트
    nxt = save_spike_events(_result({"spao": SPIKE | {"period": "2026-11-23"}}))
    assert len(nxt) == 1
    saved = [e for e in repo.recent_events(days=7) if e.change_type == ChangeType.TREND_SPIKE]
    assert len(saved) == 3


def test_main_alerts_ops_and_returns_1_on_failure(monkeypatch, tmp_path):
    from app.infra.notify import slack
    from app.site_monitor import trend

    alerts = []
    monkeypatch.setattr(slack, "send_ops_alert", lambda msg: alerts.append(msg))
    monkeypatch.delenv("NCP_INSIGHT_KEY_ID", raising=False)
    monkeypatch.delenv("NCP_INSIGHT_KEY", raising=False)
    monkeypatch.setattr(trend, "OUT_DIR", tmp_path)
    assert trend.main(["--no-events"]) == 1
    assert len(alerts) == 1 and "trend" in alerts[0]


def test_trend_label_everywhere():
    from app.infra.dashboard import CHANGE_LABEL as dash
    from app.infra.notify.render import CHANGE_LABEL as mail

    assert dash[ChangeType.TREND_SPIKE] == mail["trend_spike"] == "쇼핑 클릭 급증"


def test_site_pipeline_skips_brand_without_urls():
    # 봇 차단 브랜드는 실패(슬랙 알림) 대신 CLI 의 '건너뜀' 경로로 간다
    with pytest.raises(NotImplementedError, match="직접 수집 대상 아님"):
        pipeline.run(Brand.SPAO)
