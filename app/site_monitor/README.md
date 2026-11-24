# 🅰 site_monitor — 상품·가격 수집 & 변화 감지 (담당: A)

브랜드 상품의 **이름·가격**을 매일 네이버 쇼핑 검색 API로 받고, 지난번과 비교해 `ChangeEvent`를 만든다.

## 왜 공식몰을 직접 수집하지 않나 (2026-11-24 확인)

| 사이트 | 확인 결과 |
|---|---|
| 스파오 `spao.com` | robots.txt: 허용 목록(Googlebot 등) 밖의 봇은 `Disallow: /` |
| 에잇세컨즈 `ssfshop.com` | robots.txt: 허용 목록 밖의 봇은 `Disallow: /` |
| 무신사 스탠다드 `musinsa.com` | 봇 접속 차단 (robots.txt 포함 403) |
| 유니클로 `uniqlo.com` | 봇 접속 차단 (연결 끊김) |

프로젝트 규칙(robots.txt 준수, 공식 API 우선)에 따라 공식 API인 네이버 쇼핑 검색으로 바꿨다.
User-Agent를 속이거나 차단을 우회하는 방식은 쓰지 않는다.

## 파일

| 파일 | 하는 일 |
|---|---|
| `shopping.py` | 네이버 쇼핑 검색 API 수집기 (`BaseCollector` 구현, `source="naver_shopping"`) |
| `differ.py` | 지난번 상태 vs 오늘 → 신상품 / 가격 변동(±5% 이상) |
| `pipeline.py` | `run(brand)` : 수집 → 변화 감지 → 저장 (CLI가 호출). 첫 수집은 기준선만 저장 |

- 입력: `config/brands.yaml`의 `site.shopping` (검색어 `queries`, 브랜드 칸 `brand_names`, 공식 판매처 `malls`)
- 출력: `list[RawItem]` → D의 `infra.storage`, `list[ChangeEvent]` → C의 `analysis`
- 키: `.env`의 `NAVER_SHOP_CLIENT_ID/SECRET` (비우면 뉴스용 `NAVER_CLIENT_ID/SECRET`), 주소는 `NAVER_SHOP_API_URL`
  - 기본 주소는 네이버 개발자센터(`openapi.naver.com/v1/search/shop.json`, 헤더 `X-Naver-Client-*`)
  - 다른 주소(API HUB)를 넣으면 헤더가 `X-NCP-APIGW-*`로 바뀐다
- 테스트: `tests/site_monitor/` + `tests/fixtures/naver_shopping_spao.json` (실제 API 호출 없음)

## 이 방식의 한계

- **품절·재입고를 알 수 없다.** API에 재고 정보가 없다 (`in_stock`은 항상 비어 있음).
- **메인 배너·기획전 변경을 알 수 없다.** 페이지를 보지 않는다. 프로모션은 뉴스·SNS(B)에서 잡는다.
- **가격은 네이버 쇼핑 최저가(`lprice`)다.** 여러 판매처가 묶인 상품은 공식몰이 아닌 판매처의 최저가일 수 있다.
  실제 수집 결과의 `mallName`을 보고 `brands.yaml`의 `malls`에 공식 판매처를 넣으면 그 판매처 상품만 남는다.
- **검색어당 최신 등록순 300개까지만 본다** (`max_items`). 그 밖의 상품은 가격이 바뀌어도 모른다.
- 정가·할인가 구분이 없다 (`price`만 채우고 `sale_price`는 비움).

## 아직 확인하지 못한 것 (TODO(A))

- 실제 키로 한 번도 돌리지 않았다. 응답 형식은 공식 문서 기준이다.
- NAVER API HUB에 쇼핑 검색이 있는지, 있다면 주소가 무엇인지 확인하지 못했다.
  (인증 없이 확인해 보면 `search/v1/news`·`blog` 등은 있고 `search/v1/shop`은 없다.)
- 브랜드별 `malls`(공식 판매처 이름)와, 4개 브랜드에서 실제로 몇 건이 남는지.
