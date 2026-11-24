# 경쟁사 모니터링 Agent

SPA 브랜드(스파오 / 에잇세컨즈 / 무신사 스탠다드 / 유니클로)의 사이트·뉴스·SNS를 수집해 변화를 감지하고, LLM으로 주간 인사이트를 만들어 슬랙·메일로 보낸다. 전체 설계는 [DESIGN.md](DESIGN.md).

## 폴더 = 역할

```
app/
├── core/            🤝 공통 계약 (models·config·BaseCollector)   ← 전원 리뷰, D 관리
├── site_monitor/    🅰 A  상품·가격 수집(네이버 쇼핑 검색 API) & 변화 감지
├── media_monitor/   🅱 B  뉴스·SNS 수집
├── analysis/        🅲 C  LLM 분석 & 리포트 콘텐츠
├── infra/           🅳 D  저장소·알림·통합 (PM 겸)
└── __main__.py      🅳 D  CLI 진입점
config/brands.yaml   🅱 B 주도 (site 항목은 A)
tests/<역할 폴더>/    각자 자기 폴더 테스트
.github/             🅳 D  CI·스케줄·CODEOWNERS
web/                 🅳 D  대시보드·리포트 열람 웹(Next.js → Vercel), 팀원만 로그인
```

| 역할 | 폴더 | 만드는 것 | 넘겨주는 것 → 받는 사람 |
|---|---|---|---|
| 🅰 A | [app/site_monitor/](app/site_monitor/README.md) | 네이버 쇼핑 검색 API 수집기, 전일 대비 diff(신상품·가격 변동) | `RawItem` → D 저장소 / `ChangeEvent` → C |
| 🅱 B | [app/media_monitor/](app/media_monitor/README.md) | 네이버·Google 뉴스, 인스타·유튜브, 중복 제거, 반응 급증 감지 | `RawItem` → D 저장소 / `ChangeEvent` → C |
| 🅲 C | [app/analysis/](app/analysis/README.md) | 분류·요약·인사이트 프롬프트와 파이프라인, 골든셋 평가 | `WeeklyReport` → D |
| 🅳 D | [app/infra/](app/infra/README.md) | DB·repo, 슬랙/메일 렌더링·발송, CLI, GitHub Actions | 최종 리포트 → 팀 슬랙·메일 |

데이터 흐름: `A·B 수집 → RawItem → (diff) → ChangeEvent → C 분류·요약·인사이트 → WeeklyReport → D 발송`

## 협업 규칙
- **자기 폴더만 수정.** 다른 폴더가 필요하면 담당자에게 요청하거나 PR로 리뷰 받기
- `app/core/` 변경 = 4명 전원 승인 (데이터 계약이 바뀌므로)
- DB는 직접 만지지 않고 `app/infra/storage/repo.py` 함수만 호출
- 브랜치: `feat/<폴더>-<내용>` (예: `feat/site_monitor-uniqlo`), PR 리뷰 짝 A↔B, C↔D
- `.github/CODEOWNERS`에 역할별 담당자가 지정돼 있어 PR을 열면 리뷰어가 자동 지정됨

### main 브랜치 보호 규칙
`main`은 GitHub 규칙(Settings → Rules → Rulesets → `main 보호`)으로 보호된다. 아래를 만족해야 병합된다.

| 규칙 | 내용 |
|---|---|
| PR 필수 | `main`에 직접 push 불가. 브랜치를 만들어 PR로만 병합 |
| 승인 1명 이상 | 승인이 1개 이상 있어야 병합 가능 |
| 코드 오너 승인 | 바뀐 파일의 CODEOWNERS 담당자가 승인해야 함 |
| 새 커밋 = 재승인 | 승인 후 커밋을 추가하면 기존 승인이 취소됨 |
| CI 통과 | `ci` 워크플로의 `test` 작업(ruff + pytest)이 성공해야 함 |
| 강제 push·삭제 금지 | `main`에 force push, 브랜치 삭제 불가 |

- **예외:** 레포 관리자(D)는 PR 화면에서만 규칙을 건너뛰고 병합할 수 있다. D 폴더는 코드 오너가 본인이라 셀프 승인이 안 되기 때문. 관리자도 `main`에 직접 push는 불가.
- **`app/core/` 4명 승인은 자동 강제가 아니다.** GitHub는 코드 오너 중 1명만 승인하면 통과시키므로, 4명 전원 승인은 팀 약속으로 지킨다.
- 작업 흐름: `git switch -c feat/<폴더>-<내용>` → 커밋 → `git push -u origin <브랜치>` → GitHub에서 PR 생성 → CI 통과 + 리뷰 승인 → 병합

## 시작하기
```powershell
python -m venv .venv; .venv\Scripts\activate
pip install -e ".[dev]" -c constraints.txt
copy .env.example .env      # 키 채우기 (각 값을 어디서 받는지는 파일 안 설명 참고)
python -m app init-db
pytest -q
```

### 로컬에서 실제 키로 돌려 보기
`.env`의 `DATABASE_URL`을 비워 두면 로컬 SQLite 파일(`data/monitor.db`)에만 기록되어 운영 데이터와 섞이지 않는다.
슬랙·메일 값을 비워 두면 아무것도 발송되지 않는다.

```powershell
python -m app doctor                              # 빠진 값·여러 줄로 들어간 값 확인
python -m app collect --source news --brand spao  # 뉴스 (네이버 + 구글)
python -m app collect --source sns --brand spao   # 인스타 + 유튜브
python -m app collect --source site --brand spao  # 상품·가격 (쇼핑 검색 키가 있을 때만)
python -m app classify                            # 수집한 이벤트 분류 (LLM 호출, 비용 발생)
python -m app status                              # 수집·분류 현황
```

| 명령 | 담당 |
|---|---|
| `python -m app collect --source site --brand spao` | A |
| `python -m app collect --source news` / `--source sns` | B |
| `python -m app classify` | C |
| `python -m app report --week 2026-W47 --dry-run` | C + D |
| `python -m app dashboard` / `python -m app prune` | D |
| `python -m app seed` / `seed --clear` (발송 리허설용 샘플) | D |
| `python -m app doctor` (설정 점검) / `python -m app status` (운영 현황) | D |

운영 설정(Secrets, 운영 DB)과 운영 경고의 뜻은 [app/infra/README.md](app/infra/README.md) 참고.
| `python -m app seed --demo` (화면 확인용 샘플) | D |
