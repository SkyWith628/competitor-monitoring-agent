# robots.txt 확인 결과 (2026-11-23, 담당: A)

우리 봇 User-Agent: `CompetitorMonitorBot/0.1` — 어느 사이트의 허용 명단에도 없으므로 `User-agent: *` 규칙을 따른다.

| 브랜드 | 사이트 | `*` 규칙 | 우리 봇 수집 | 비고 |
|---|---|---|---|---|
| 스파오 | spao.com | `Disallow: /` | ❌ 전체 금지 | 검색엔진·AI 검색봇만 `/i/item`, `/c/ctg`, `/p/planshop` 등 허용 |
| 에잇세컨즈 | ssfshop.com | `Disallow: /` | ❌ 전체 금지 | 검색엔진·AI 봇만 허용 (`/secured/`, `/raffle/` 제외) |
| 무신사 스탠다드 | musinsa.com | `Disallow: /` | ❌ 전체 금지 | "명단에 없는 신규 봇은 자동 차단" 정책을 robots.txt에 명시 |
| 유니클로 | uniqlo.com | 일부만 금지 | ✅ 대부분 허용 | 아래 금지 경로 제외. `/kr/` 전용 추가 규칙 없음 |

## 유니클로 금지 경로 (수집 URL에 넣지 말 것)
- 장바구니 `/*/*/cart`, CMS `/*/*/cms/`, 사이즈 `/*/*/size/`, 리뷰 작성 `/*/*/*/reviews/new`
- 검색 `/*/*/search?q=`
- 목록 필터 파라미터: `categoryIds`, `flagCodes`, `inventoryCondition`, `priceRanges`, `sizeCodes`, `storeId`, `follow=no`
- `/feature/` 페이지의 `colorCodes`, `tag*` 필터 파라미터
- 한국 사이트맵: `https://www.uniqlo.com/kr/ko/sitemap_kr-ko_index.xml`

## 대체 소스 조사 (차단된 3개 브랜드용, 2026-11-23)
| 소스 | 상태 | 비고 |
|---|---|---|
| 네이버 쇼핑 검색 API (공식) | 🟢 1순위 후보 | Client ID/Secret 필요(B 담당 발급). 가격(lprice)·판매처(mallName) 제공, **재고/품절 정보 없음**. 호출 한도·필드는 공식 문서 미확인(developers.naver.com 접속 불가) |
| 29cm.co.kr | 🟡 robots 허용 | `*` Allow / (embed·my-page·order·auth 등 제외). 입점 여부·이용약관 미확인. 검색 URL `/search?keyword=` 는 404 |
| ssg.com | ❌ | `*` Disallow / |
| lotteon.com | ❌ | `*` Disallow / |
| 비공식 엔드포인트 (`ns-portal.shopping.naver.com` 등) | ⛔ 사용 금지 | 비공식·약관 위반 소지 |

## 원칙
- 허용 명단에 있는 User-Agent(Googlebot, ClaudeBot 등)를 흉내 내지 않는다.
- robots.txt 외에 **이용약관**도 별도로 확인해야 한다 (미확인).
- 판정은 `app/site_monitor/robots.py` (와일드카드 `*`·`$` 지원). 표준 `urllib.robotparser`는 와일드카드를 몰라 유니클로 금지 경로를 허용으로 잘못 판정한다.
