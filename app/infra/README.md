# 🅳 infra — 플랫폼·저장소·알림·통합 (담당: D, PM 겸)

모든 역할의 코드가 **올라타는 바닥**과 **최종 출구**를 맡는다. 1주차 1~2일차에 가장 먼저 뼈대를 깔아 A·B·C가 바로 붙을 수 있게 한다.

| 파일 | 하는 일 |
|---|---|
| `storage/db.py` | DB 연결·테이블 생성 (SQLite → 운영 시 PostgreSQL) |
| `storage/repo.py` | 저장/조회 함수 — **A·B·C는 DB를 직접 만지지 않고 이 함수만 호출** |
| `notify/render.py` | `WeeklyReport` → Markdown / HTML / 슬랙 Block Kit |
| `notify/slack.py` | Slack Incoming Webhook 발송 (리포트 채널 / 운영 경고 채널) |
| `notify/email.py` | SMTP HTML 메일 발송 |
| `notify/templates/` | `weekly.html.j2`, `weekly.md.j2` |
| `../__main__.py` | CLI 진입점 (`python -m app ...`) |
| `../../.github/workflows/` | daily / weekly / ci 자동 실행 |

- repo 함수 시그니처를 바꿀 때는 호출하는 쪽(A·B·C)에 먼저 공유
- 비밀값은 `.env`(로컬)·GitHub Secrets(운영)에만 둔다
- 운영 경고(수집 0건, 예외, LLM 실패)는 `#monitor-ops` 채널로
