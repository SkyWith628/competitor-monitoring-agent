# 경쟁사 모니터링 Agent

SPA 브랜드(스파오 / 에잇세컨즈 / 무신사 스탠다드 / 유니클로)의 사이트·뉴스·SNS를 수집해 변화를 감지하고, LLM으로 주간 인사이트를 만들어 슬랙·메일로 보낸다. 전체 설계는 [DESIGN.md](DESIGN.md).

## 폴더 = 역할

```
app/
├── core/            🤝 공통 계약 (models·config·BaseCollector)   ← 전원 리뷰, D 관리
├── site_monitor/    🅰 A  사이트 수집 & 변화 감지
├── media_monitor/   🅱 B  뉴스·SNS 수집
├── analysis/        🅲 C  LLM 분석 & 리포트 콘텐츠
├── infra/           🅳 D  저장소·알림·통합 (PM 겸)
└── __main__.py      🅳 D  CLI 진입점
config/brands.yaml   🅱 B 주도 (사이트 URL은 A)
tests/<역할 폴더>/    각자 자기 폴더 테스트
.github/             🅳 D  CI·스케줄·CODEOWNERS
```

| 역할 | 폴더 | 만드는 것 | 넘겨주는 것 → 받는 사람 |
|---|---|---|---|
| 🅰 A | [app/site_monitor/](app/site_monitor/README.md) | 브랜드별 공식몰 수집기 4개, 셀렉터, 전일 대비 diff | `RawItem` → D 저장소 / `ChangeEvent` → C |
| 🅱 B | [app/media_monitor/](app/media_monitor/README.md) | 네이버·Google 뉴스, 인스타·유튜브, 중복 제거, 반응 급증 감지 | `RawItem` → D 저장소 / `ChangeEvent` → C |
| 🅲 C | [app/analysis/](app/analysis/README.md) | 분류·요약·인사이트 프롬프트와 파이프라인, 골든셋 평가 | `WeeklyReport` → D |
| 🅳 D | [app/infra/](app/infra/README.md) | DB·repo, 슬랙/메일 렌더링·발송, CLI, GitHub Actions | 최종 리포트 → 팀 슬랙·메일 |

데이터 흐름: `A·B 수집 → RawItem → (diff) → ChangeEvent → C 분류·요약·인사이트 → WeeklyReport → D 발송`

## 협업 규칙
- **자기 폴더만 수정.** 다른 폴더가 필요하면 담당자에게 요청하거나 PR로 리뷰 받기
- `app/core/` 변경 = 4명 전원 승인 (데이터 계약이 바뀌므로)
- DB는 직접 만지지 않고 `app/infra/storage/repo.py` 함수만 호출
- 브랜치: `feat/<폴더>-<내용>` (예: `feat/site_monitor-uniqlo`), PR 리뷰 짝 A↔B, C↔D
- `.github/CODEOWNERS`의 `TEAM_X_GITHUB_ID`를 실제 아이디로 바꾸면 PR 리뷰어가 자동 지정됨

## 시작하기
```powershell
python -m venv .venv; .venv\Scripts\activate
pip install -e ".[dev]"
copy .env.example .env      # 키 채우기
python -m app init-db
pytest -q
```

| 명령 | 담당 |
|---|---|
| `python -m app collect --source site --brand spao` | A |
| `python -m app collect --source news` / `--source sns` | B |
| `python -m app classify` | C |
| `python -m app report --week 2026-W47 --dry-run` | C + D |
