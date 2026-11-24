"""[A] 상품 파이프라인 — 첫 수집은 기준선만, 다음부터 신상품·가격 변동 이벤트."""
from app.core.models import Brand, ChangeType, RawItem, SourceType
from app.site_monitor import pipeline


def _product(pid: str, price: int) -> RawItem:
    return RawItem(brand=Brand.SPAO, source_type=SourceType.SITE_PRODUCT, source="naver_shopping",
                   external_id=pid, url=f"https://example.com/{pid}", title=f"상품 {pid}", price=price)


def test_first_run_saves_baseline_without_events(monkeypatch):
    today, db, saved_events = [_product("A", 39900), _product("B", 19900)], {}, []

    class Collector:
        source = "naver_shopping"

        def __init__(self, brand, cfg):
            pass

        def collect(self):
            return list(today)

    monkeypatch.setattr(pipeline, "brands_config", lambda: {"spao": {"site": {}}})
    monkeypatch.setattr(pipeline, "NaverShoppingCollector", Collector)
    monkeypatch.setattr(pipeline.repo, "latest_items", lambda brand, source: dict(db))
    monkeypatch.setattr(pipeline.repo, "save_items", lambda items: db.update({i.external_id: i for i in items}))
    monkeypatch.setattr(pipeline.repo, "save_events", lambda events: saved_events.extend(events))

    assert pipeline.run(Brand.SPAO) == []  # 첫 수집: 상품 2개를 저장만 한다
    assert set(db) == {"A", "B"} and saved_events == []

    today[:] = [_product("A", 29900), _product("B", 19900), _product("C", 49900)]  # 다음 날
    events = pipeline.run(Brand.SPAO)
    assert {(e.change_type, e.title) for e in events} == {(ChangeType.PRICE_DOWN, "상품 A"),
                                                          (ChangeType.NEW_PRODUCT, "상품 C")}
    assert next(e for e in events if e.change_type == ChangeType.PRICE_DOWN).detail == {"before": 39900, "after": 29900}
