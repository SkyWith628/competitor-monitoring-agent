# 🅳 infra — 플랫폼·저장소·알림·통합 (담당: D, PM 겸)

모든 역할의 코드가 **올라타는 바닥**과 **최종 출구**를 맡는다. 1주차 1~2일차에 가장 먼저 뼈대를 깔아 A·B·C가 바로 붙을 수 있게 한다.

| 파일 | 하는 일 |
|---|---|
| `storage/db.py` | DB 연결·테이블 생성 (로컬 SQLite / 운영 Supabase PostgreSQL) |
| `storage/repo.py` | 저장/조회 함수 — **A·B·C는 DB를 직접 만지지 않고 이 함수만 호출** |
| `notify/render.py` | `WeeklyReport` → Markdown / HTML / 슬랙 Block Kit |
| `notify/slack.py` | Slack Incoming Webhook 발송 (리포트 채널 / 운영 경고 채널) |
| `notify/email.py` | SMTP HTML 메일 발송 |
| `notify/templates/` | `weekly.html.j2`, `weekly.md.j2` |
| `../__main__.py` | CLI 진입점 (`python -m app ...`) |
| `../../.github/workflows/` | daily / weekly / ci 자동 실행 |

- LLM 토큰 로깅은 `repo.save_llm_call(model, input_tokens, output_tokens)` (C가 `llm_client.log_usage` 에서 호출)
- 수집기가 예외 없이 0건만 돌려줘도 `collect` 가 `#monitor-ops` 로 경고 (뉴스는 3일 연속 신규 0건일 때)
- repo 함수 시그니처를 바꿀 때는 호출하는 쪽(A·B·C)에 먼저 공유
- 비밀값은 `.env`(로컬)·GitHub Secrets(운영)에만 둔다
- 운영 경고(수집 0건, 예외, LLM 실패)는 `#monitor-ops` 채널로

## 운영 DB (Supabase)

`DATABASE_URL` 이 있으면 PostgreSQL, 없으면 로컬 SQLite 파일(`DB_PATH`)을 쓴다. 로컬 개발은 그대로 SQLite 로 충분하다.

1. Supabase 프로젝트 생성 → 상단 **Connect** → **Session pooler** 의 연결 문자열 복사
   (`postgresql://postgres.<프로젝트>:<비밀번호>@aws-0-<리전>.pooler.supabase.com:5432/postgres`)
   - **Direct connection 은 쓰지 않는다** — IPv6 전용이라 GitHub Actions 러너에서 접속되지 않는다
   - 비밀번호에 `@ : / #` 같은 특수문자가 있으면 URL 인코딩(`@` → `%40`)
2. GitHub 저장소 **Settings → Secrets and variables → Actions** 에 `DATABASE_URL` 로 등록
3. 테이블은 워크플로의 `python -m app init-db` 가 만든다 (여러 번 실행해도 안전)

`init-db` 는 모든 테이블에 RLS 를 켠다. 정책이 없으므로 Supabase REST API(anon 키)로는 읽고 쓸 수 없고, 연결 문자열을 가진 이 앱만 접근한다.
로컬에서 운영 DB 를 볼 때만 `.env` 에 `DATABASE_URL` 을 넣는다 — 넣은 채로 `collect` 를 돌리면 운영 데이터에 쓰인다.

PostgreSQL 로 테스트: `TEST_DATABASE_URL=postgresql://... pytest tests/infra` (테이블을 지우므로 일회용 DB 만 지정)
