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

## ⚠ 키를 구할 수 있는지부터 확인할 것 (2026-11-24)

**지금 팀이 가진 키로는 이 수집이 동작하지 않는다.** 운영 실행에서 4개 브랜드 모두 `401 Authentication failed`.

- 팀의 네이버 키는 **NAVER API HUB** 키다(뉴스 수집용). API HUB의 검색 API는
  블로그·뉴스·백과사전·카페글·지식iN·웹문서·이미지·지역만 제공하고 **쇼핑 검색은 없다**
  ([상품 안내](https://www.ncloud.com/product/applicationService/naverApiHub)의 "제공 API" 표).
  API HUB의 "쇼핑 인사이트"는 분야별 클릭 추이 지표라, 상품명·가격을 주지 않는 다른 API다.
- 쇼핑 검색은 예전 **네이버 개발자센터**(`openapi.naver.com`) API에만 있다. 그런데 개발자센터의 검색 API 소개
  페이지는 이제 이용신청을 API HUB로 보낸다. 새 애플리케이션으로 쇼핑 검색 키를 받을 수 있는지는 확인하지 못했다.

그래서 길은 둘 중 하나다.

1. 팀이나 회사에 **예전에 만든 개발자센터 애플리케이션**(검색 API 사용 설정됨)이 있으면, 그 Client ID·Secret을
   `NAVER_SHOP_CLIENT_ID`·`NAVER_SHOP_CLIENT_SECRET`에 넣는다. 개발자센터 → Application → 내 애플리케이션에서 확인.
   없다면 개발자센터에서 애플리케이션 등록을 직접 시도해 본다(사용 API에 "검색"이 보이는지).
2. 구할 수 없으면 상품 수집은 쓸 수 없다. daily 워크플로의 `collect site` 단계를 빼서 매일 실패 표시가 뜨지 않게 한다.

## 아직 확인하지 못한 것 (TODO(A))

- 실제 키로 수집에 성공한 적이 없다. 응답 형식은 공식 문서 기준이고, 테스트 데이터는 그 형식대로 만든 가짜다.
- 기존 개발자센터 애플리케이션이 지금도 동작하는지, 언제까지 동작하는지.
- 브랜드별 `malls`(공식 판매처 이름)와, 4개 브랜드에서 실제로 몇 건이 남는지.
