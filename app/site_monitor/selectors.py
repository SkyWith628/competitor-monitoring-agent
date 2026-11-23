"""브랜드별 셀렉터/API 경로. 사이트 개편 시 코드 대신 여기를 고친다. (담당: A)

키 설명 (필수: product_card, name, price)
  product_card  상품 카드 1개를 감싸는 요소      name        카드 안 상품명
  price         정가(또는 단일 가격)             sale_price  할인가 (없으면 "")
  sold_out      카드 안에 있으면 품절인 요소      link        상세 링크 (기본: 카드 안 첫 a[href])
  image         이미지 (기본 img)               promo       기획전/배너 영역 (SITE_PAGE 스냅샷용)
  id_attr       상품코드가 든 카드 속성 (예: data-product-id)
  id_regex      링크에서 상품코드를 뽑는 정규식 (그룹 1)
⚠ 아래 값은 실사이트에서 직접 확인해 채워야 한다. 비어 있으면 수집 시 명확한 에러가 난다.
"""

_EMPTY = {"product_card": "", "name": "", "price": "", "sale_price": "", "sold_out": "",
          "link": "", "image": "", "promo": "", "id_attr": "", "id_regex": ""}

SELECTORS: dict[str, dict[str, str]] = {
    "spao": dict(_EMPTY),
    "8seconds": dict(_EMPTY),
    "musinsa_standard": dict(_EMPTY),
    # 2026-11-23 /kr/ko/men/tops 에서 확인. JS 렌더링 필요(render: js).
    # - 목록엔 현재 판매가 1개만 노출(정가 취소선 없음) → sale_price 없음. 인하는 가격 변동으로 감지.
    # - 같은 상품도 색상 그룹(/00, /01)마다 가격이 달라 ID에 그룹 번호까지 포함.
    # - 품절 상품은 목록에 표시되지 않는 것으로 보임 → sold_out 없음 (미확인).
    "uniqlo": {**_EMPTY,
               "product_card": "a.product-tile__link",
               "name": "h3",
               "price": "h3 + div",
               "image": ".product-tile__carousel-wrapper img",
               "id_regex": r"/products/(E\d+-\d+/\d+)"},
    # 2026-11-23 /product/list.html?cate_no=101 에서 확인. 카페24 서버 렌더링(render 불필요).
    # - 카드 2종: 상단 추천 3개(.price_box .price) / 일반(li[rel="판매가"] .m_item). 둘 다 "현재 판매가"만 쓴다.
    #   소비자가(취소선)는 카드에 없을 수 있어 사용하지 않음 → 인하는 가격 변동으로 감지.
    # - sold_out 은 카페24 표준 품절 아이콘(.icon img[alt*=품절])을 가정한 값. 품절 상품이 있는 페이지로 미확인.
    "whoau": {**_EMPTY,
              "product_card": 'li[id^="anchorBoxId_"]',
              "name": ".name span",
              "price": 'li[rel="판매가"] .m_item, .price_box .price',
              "sold_out": '.icon img[alt*="품절"]',
              "image": ".prdImg img",
              "id_regex": r"/product/[^/]+/(\d+)/"},
}
