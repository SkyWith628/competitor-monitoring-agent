"""[A] differ 규칙 테스트 — 예시. 파서 테스트는 tests/fixtures/html/ 의 저장된 HTML로 작성."""
from app.core.models import Brand, ChangeType, RawItem, SourceType
from app.site_monitor.differ import diff


def _item(eid: str, price: int, in_stock: bool = True) -> RawItem:
    return RawItem(brand=Brand.SPAO, source_type=SourceType.SITE_PRODUCT, source="spao.com",
                   external_id=eid, url=f"https://example.com/{eid}", title=eid,
                   price=price, in_stock=in_stock)


def test_new_product():
    events = diff({}, [_item("A1", 29900)])
    assert [e.change_type for e in events] == [ChangeType.NEW_PRODUCT]


def test_price_down_and_sold_out():
    events = diff({"A1": _item("A1", 39900)}, [_item("A1", 29900, in_stock=False)])
    assert {e.change_type for e in events} == {ChangeType.PRICE_DOWN, ChangeType.SOLD_OUT}


def test_small_price_change_ignored():
    assert diff({"A1": _item("A1", 30000)}, [_item("A1", 29900)]) == []
