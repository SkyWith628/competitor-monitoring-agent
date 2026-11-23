# 🅰 site_monitor — 사이트 수집 & 변화 감지 (담당: A)

**네이버 쇼핑 검색 API**로 브랜드 상품·가격을 매일 수집하고, 전날과 비교해 **신상품·가격 변동** `ChangeEvent`를 만든다.

> 공식몰이 robots.txt로 봇을 차단해 직접 수집은 보류했다([ROBOTS.md](ROBOTS.md)). 그래서 품절·재입고·기획전은 현재 범위 밖이다.

| 파일 | 하는 일 |
|---|---|
| `collectors/naver_shopping.py` | 네이버 쇼핑 API 수집기 (4개 브랜드 공용, `brands.yaml`의 `site.via: naver_shopping`) |
| `collectors/spao.py` 외 3개 | 브랜드별 공식몰 수집기 (보류, 공식몰 수집이 허용될 때 사용) |
| `selectors.py` | 브랜드별 CSS 셀렉터·API 경로 모음 (사이트 개편 시 여기만 수정) |
| `differ.py` | 전일 스냅샷 vs 오늘 → 신상품/가격변동 (품절·재입고·기획전 로직은 있으나 현재 데이터가 없어 이벤트 미발생) |
| `pipeline.py` | `run(brand)` : 수집 → 저장 → 변화감지 (CLI가 호출) |

- 입력: `config/brands.yaml`의 `site` 항목 (`naver_shopping.queries/brands/malls`)
- 출력: `list[RawItem]` → D의 `infra.storage`, `list[ChangeEvent]` → C의 `analysis`
- 테스트: `tests/site_monitor/` + `tests/fixtures/html/` (저장해둔 HTML로 파서 테스트, 실사이트 접속 X)
- 규칙: robots.txt 금지 경로 수집 금지, 요청 간격 ≥ 3초(공식몰) / API 호출 제한 준수(네이버)

## 현재 범위와 한계
| 항목 | 상태 | 이유 |
|---|---|---|
| 신상품 | 지원 | 전일에 없던 `productId` 감지 |
| 가격 변동 | 지원(제한) | `lprice`는 판매처 중 최저가. `malls`로 공식 판매처를 지정해야 신뢰 가능 |
| 품절·재입고 | 범위 밖 | API에 재고 정보 없음 (`in_stock=None`) |
| 기획전·배너 | 범위 밖 | API에 프로모션 데이터 없음 |

재개 조건: 공식몰 수집이 허용되면 브랜드별 수집기를 다시 활성화한다.
