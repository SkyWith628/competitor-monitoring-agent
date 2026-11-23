# 🅰 site_monitor — 사이트 수집 & 변화 감지 (담당: A)

공식몰 상품 목록을 **직접 수집**(학습용)하고, 전날과 비교해 **신상품·가격 변동** `ChangeEvent`를 만든다.

> 현재 직접 수집 대상은 **WHO.A.U(후아유) 1곳**이다. 나머지 4개 브랜드는 robots.txt 차단·접속 차단·네이버 API 키 발급 불가로 수집하지 않는다. 이유와 확인 결과는 [WHY_WHOAU.md](WHY_WHOAU.md), [ROBOTS.md](ROBOTS.md).

| 파일 | 하는 일 |
|---|---|
| `collectors/base_site.py` | 공식몰 수집 공통 로직 (robots.txt 확인 → 요청 → 파싱) |
| `collectors/whoau.py` | **WHO.A.U 수집기** (카페24, 요청 간격 5초) |
| `collectors/spao.py` 외 3개 | 나머지 브랜드 수집기 (셀렉터 비어 있음 — 수집이 허용·가능해질 때 사용) |
| `collectors/naver_shopping.py` | 네이버 쇼핑 API 수집기 (**현재 사용 불가**: 키 발급 불가, 코드만 보존) |
| `selectors.py` | 브랜드별 CSS 셀렉터 (사이트 개편 시 여기만 수정) |
| `parser.py` | HTML → `RawItem` 변환 (셀렉터 기반) |
| `differ.py` | 전일 스냅샷 vs 오늘 → 신상품/가격변동 |
| `pipeline.py` | `run(brand)` : 수집 → 저장 → 변화감지 (CLI가 호출). `site.urls`가 빈 브랜드는 실패 대신 '건너뜀' 처리 |
| `trend.py` | **네이버 쇼핑인사이트 클릭 추이** (5개 브랜드 공통, 이벤트 없이 파일 저장만) |

- 입력: `config/brands.yaml`의 `site.urls` (후아유: `cate_no=101` 1개)
- 출력: `list[RawItem]` → D의 `infra.storage`, `list[ChangeEvent]` → C의 `analysis`
- 테스트: `tests/site_monitor/` + `tests/fixtures/html/` (저장해 둔 HTML로 파서 테스트, 실사이트 접속 X)

## 수집 규칙 (서버에 부담을 주지 않기 위해)
- **하루 1회**, 요청 간격 **5초 이상**(WHO.A.U), 그 외 공식몰은 3초 이상
- User-Agent에 프로그램임을 밝힌다 (`CompetitorMonitorBot/0.1`)
- robots.txt 금지 경로는 수집하지 않는다. **정렬·필터·검색 URL**(`sort=`, `filter=`, `search.html`)은 URL에 넣지 않는다
- 403/429 응답이 오면 중단한다. 차단을 우회하지 않는다

## 현재 범위와 한계
| 항목 | 상태 | 이유 |
|---|---|---|
| 신상품 | 지원 | 전일에 없던 상품번호 감지 |
| 가격 변동 | 지원 | 목록의 현재 판매가만 사용(정가 취소선 미사용) → 어제·오늘 가격 비교로 감지 |
| 품절·재입고 | 부분 | 품절 셀렉터는 카페24 표준 아이콘을 가정한 값. 품절 상품이 있는 페이지로 미검증 |
| 기획전·배너 | 범위 밖 | 셀렉터 미작성 |
| 브랜드 범위 | WHO.A.U 1곳 | 스파오·에잇세컨즈·무신사 스탠다드는 robots.txt 차단, 유니클로는 접속 차단 |

## 쇼핑인사이트 추이 (1단계: 저장만)
직접 수집이 막힌 4개 브랜드도 **관심도 추이**는 비교할 수 있게, 5개 브랜드의 패션의류 분야 키워드 클릭 추이를 하루 1회 받는다.

- 실행: `python -m app.site_monitor.trend [--days 30]` → `data/trends/shopping_insight-<날짜>.json`
- 키: `.env`에 `NCP_INSIGHT_KEY_ID` / `NCP_INSIGHT_KEY` (NCP 콘솔에서 쇼핑인사이트 API 이용 신청. B의 키와 이름을 분리)
- 키워드: `brands.yaml`의 `site.shopping_insight.keyword`
- 5개 브랜드를 **한 요청**(그룹 5개)으로 보낸다. `ratio`는 요청마다 최댓값 100으로 다시 맞춰지므로 한 응답 안에서만 비교한다.
- 급증 후보: 어제 값이 직전 7일 평균의 1.5배 이상이고 5 이상일 때. **파일·로그에만 남기고 `ChangeEvent`는 만들지 않는다.**
- 2단계(팀 협의 필요): `ChangeType.TREND_SPIKE` 추가 → 알림 라벨(`render.py`, 대시보드, `web/lib/format.ts`)과 C의 분류 기준을 함께 고친다.
- 실호출 확인(2026-11-23): 응답 `title`이 요청의 `name`(브랜드 값)과 같게 오고, 100은 **5개 그룹 전체** 최댓값 기준이다(스파오만 100) → 같은 응답 안에서 브랜드 간 비교가 된다.
- 한계: 상대값이라 판매량·매출이 아니다. 신상품·가격 정보는 없다.

## 새 사이트를 추가하려면
1. `ROBOTS.md` 기준으로 robots.txt 허용·실제 응답·서버 렌더링 여부를 확인한다.
2. `selectors.py`에 셀렉터를 채우고, 저장한 HTML을 `tests/fixtures/html/`에 넣어 파서 테스트를 만든다.
3. `collectors/`에 수집기를 만들고 `collectors/__init__.py`와 `brands.yaml`에 등록한다. (`Brand` 추가 시 대시보드·샘플 데이터·메일 표의 브랜드 수 가정도 함께 확인)
