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
    "uniqlo": dict(_EMPTY),
}
