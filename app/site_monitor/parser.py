"""HTML → RawItem 파서. 셀렉터는 selectors.py 에서 받는다 (실사이트 접속 없이 테스트 가능). 담당: A"""
from __future__ import annotations

import hashlib
import re
from urllib.parse import urljoin

from selectolax.lexbor import LexborHTMLParser as HTMLParser
from selectolax.lexbor import LexborNode as Node

from app.core.models import Brand, RawItem, SourceType

REQUIRED_KEYS = ("product_card", "name", "price")


def parse_price(text: str | None) -> int | None:
    """'39,900원' → 39900. 숫자가 없으면 None."""
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def _text(node: Node | None) -> str | None:
    return node.text(strip=True) if node is not None else None


def _first(card: Node, selector: str) -> Node | None:
    return card.css_first(selector) if selector else None


def _external_id(card: Node, link: str, selectors: dict[str, str]) -> str:
    attr = selectors.get("id_attr", "")
    if attr and card.attributes.get(attr):
        return card.attributes[attr]
    pattern = selectors.get("id_regex", "")
    if pattern and (m := re.search(pattern, link)):
        return m.group(1) if m.groups() else m.group(0)
    return hashlib.sha1(link.encode()).hexdigest()[:16]


def parse_products(html: str, *, brand: Brand, source: str, page_url: str,
                   selectors: dict[str, str]) -> list[RawItem]:
    missing = [k for k in REQUIRED_KEYS if not selectors.get(k)]
    if missing:
        raise ValueError(f"{brand.value}: selectors.py 에 {missing} 셀렉터가 비어 있음")

    items: list[RawItem] = []
    for card in HTMLParser(html).css(selectors["product_card"]):
        name = _text(_first(card, selectors["name"]))
        price = parse_price(_text(_first(card, selectors["price"])))
        if not name or price is None:
            continue  # 광고 슬롯 등 상품이 아닌 카드
        link_node = _first(card, selectors.get("link", "")) or card.css_first("a[href]")
        if link_node is None and card.attributes.get("href"):
            link_node = card  # 카드 자체가 <a href> 인 경우 (유니클로)
        href = (link_node.attributes.get("href") if link_node else None) or page_url
        url = urljoin(page_url, href)
        sale = parse_price(_text(_first(card, selectors.get("sale_price", ""))))
        img = _first(card, selectors.get("image") or "img")
        # 지연 로딩 이미지는 src 대신 data-src 에 주소가 있다
        img_src = (img.attributes.get("src") or img.attributes.get("data-src")) if img else None
        sold_out = bool(selectors.get("sold_out")) and card.css_first(selectors["sold_out"]) is not None
        items.append(RawItem(
            brand=brand, source_type=SourceType.SITE_PRODUCT, source=source,
            external_id=_external_id(card, url, selectors), url=url, title=name,
            price=price, sale_price=sale if sale and sale < price else None,
            in_stock=not sold_out,
            image_url=urljoin(page_url, img_src) if img_src else None,
        ))
    return items


def parse_page(html: str, *, brand: Brand, source: str, page_url: str,
               selectors: dict[str, str]) -> RawItem | None:
    """기획전/배너 영역 텍스트 스냅샷. 'promo' 셀렉터가 없으면 None."""
    sel = selectors.get("promo", "")
    if not sel:
        return None
    nodes = HTMLParser(html).css(sel)
    if not nodes:
        return None
    body = " ".join(n.text(separator=" ", strip=True) for n in nodes)
    return RawItem(
        brand=brand, source_type=SourceType.SITE_PAGE, source=source,
        external_id=hashlib.sha1(page_url.encode()).hexdigest()[:16],
        url=page_url, title=f"{brand.value} 프로모션 영역", body=body,
    )
