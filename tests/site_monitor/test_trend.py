"""[A] 쇼핑인사이트 추이 수집 테스트 — 가짜 응답만 사용, 실 API 호출 없음."""
import json
from datetime import date

import pytest

from app.core.config import brands_config
from app.core.models import Brand
from app.site_monitor import pipeline
from app.site_monitor.trend import ShoppingInsightTrend, brand_keywords, fill_days, save, spike


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


def test_site_pipeline_skips_brand_without_urls():
    # 봇 차단 브랜드는 실패(슬랙 알림) 대신 CLI 의 '건너뜀' 경로로 간다
    with pytest.raises(NotImplementedError, match="직접 수집 대상 아님"):
        pipeline.run(Brand.SPAO)
