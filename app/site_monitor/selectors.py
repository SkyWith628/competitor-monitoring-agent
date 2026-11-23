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
}
