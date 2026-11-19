# 🅰 site_monitor — 사이트 수집 & 변화 감지 (담당: A)

브랜드 공식몰의 **상품·가격·품절·기획전**을 매일 수집하고, 전날과 비교해 `ChangeEvent`를 만든다.

| 파일 | 하는 일 |
|---|---|
| `collectors/spao.py` 외 3개 | 브랜드별 수집기 (`BaseCollector` 구현) |
| `selectors.py` | 브랜드별 CSS 셀렉터·API 경로 모음 (사이트 개편 시 여기만 수정) |
| `differ.py` | 전일 스냅샷 vs 오늘 → 신상품/가격변동/품절/재입고/기획전 변경 |
| `pipeline.py` | `run(brand)` : 수집 → 저장 → 변화감지 (CLI가 호출) |

- 입력: `config/brands.yaml`의 `site` 항목
- 출력: `list[RawItem]` → D의 `infra.storage`, `list[ChangeEvent]` → C의 `analysis`
- 테스트: `tests/site_monitor/` + `tests/fixtures/html/` (저장해둔 HTML로 파서 테스트, 실사이트 접속 X)
- 규칙: robots.txt 금지 경로 수집 금지, 요청 간격 ≥ 3초
