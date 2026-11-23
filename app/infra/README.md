# 🅳 infra — 플랫폼·저장소·알림·통합 (담당: D, PM 겸)

모든 역할의 코드가 **올라타는 바닥**과 **최종 출구**를 맡는다.

| 파일 | 하는 일 |
|---|---|
| `storage/db.py` | DB 연결·테이블 생성. `DATABASE_URL`이 있으면 PostgreSQL, 없으면 SQLite(`DB_PATH`) |
| `storage/repo.py` | 저장/조회 함수 — **A·B·C는 DB를 직접 만지지 않고 이 함수만 호출** |
| `notify/render.py` | `WeeklyReport` → HTML / Markdown / 슬랙 Block Kit, 긴급 알림 블록 |
| `notify/slack.py` | Slack Incoming Webhook 발송 (리포트 채널 / 운영 경고 채널) |
| `notify/email.py` | SMTP 메일 발송 (텍스트 + HTML) |
| `notify/templates/` | `weekly.html.j2`, `weekly.md.j2` |
| `dashboard/` | DB 내용을 단일 HTML 대시보드로 생성 |
| `../__main__.py` | CLI 진입점 (`python -m app ...`) |
| `../../.github/workflows/` | daily / weekly / ci 자동 실행 |

- repo 함수 시그니처를 바꿀 때는 호출하는 쪽(A·B·C)에 먼저 공유
- 비밀값은 `.env`(로컬)·GitHub Secrets(운영)에만 둔다

## A·B·C가 쓰는 repo 함수

| 함수 | 쓰는 쪽 | 설명 |
|---|---|---|
| `save_items(items)` | A·B | 최신 상태 갱신 + 일자별 스냅샷 추가 |
| `latest_items(brand, source)` | A | `external_id → RawItem` (전일 대비 diff용) |
| `exists(item)` | B | 이미 저장된 항목인지 |
| `recent_posts(brand, source, limit=30)` | B | 최근 SNS 게시물 (반응 급증 기준선) |
| `save_events(events)` | A·B | 저장하면서 `event.id`를 채운다 |
| `unclassified_events()` / `update_events(events)` | C | 미분류 이벤트 조회 / 분류 결과 저장 |
| `events_of_week(week)` | C | `"2026-W47"` → 월~일 이벤트 |
| `last_report_tldr(week)` | C | 직전 리포트의 한 줄 요약 |
| `save_llm_call(model, input_tokens, output_tokens)` | C | 토큰 사용량 기록 (대시보드에 표시) |

D 내부용: `save_report`, `get_report`, `list_reports`, `recent_events`, `item_counts`, `llm_usage`,
`count_items_since`, `prune_snapshots`.

## 명령

| 명령 | 하는 일 |
|---|---|
| `python -m app init-db` | 테이블 생성 (생략해도 첫 연결 때 자동 생성) |
| `python -m app collect --source site\|news\|sns [--brand spao]` | 수집. 브랜드 하나가 실패해도 나머지는 계속하고, 실패가 있으면 종료 코드 1 |
| `python -m app classify` | 미분류 이벤트 분류. 중요도 5는 리포트 채널로 긴급 알림. 발송에 실패한 알림은 다음 실행에서 다시 시도한다 |
| `python -m app report [--week 2026-W47] [--dry-run] [--force]` | 리포트 생성 → `data/report-<주차>.html`·`.md` 저장 → 슬랙·메일 발송. 주차 기본값은 한국 시간 기준 지난주. 한 채널만 실패했으면 같은 명령을 다시 실행해 그 채널만 같은 내용으로 재발송한다. 이미 다 나간 주차는 `--force`가 있어야 새로 만들어 다시 보낸다 |
| `python -m app dashboard` | `data/dashboard.html` 생성 |
| `python -m app prune [--weeks 8]` | 오래된 스냅샷 삭제 |
| `python -m app seed [--clear]` | 발송 리허설용 샘플 이벤트 12건을 과거 주차(`2020-W01`)에 넣는다 / 지운다 |
| `python -m app status [--strict]` | 수집·분류·발송 현황. 소스별 마지막 수집 시각, 미분류 건수, 대기 중인 긴급 알림, 최근 리포트의 발송 여부. `--strict`면 36시간 넘게 멈춘 소스가 있을 때 종료 코드 1 |
| `python -m app doctor [--send-test]` | 설정 점검. 빠진 키와 그 때문에 안 되는 기능을 알려준다(값은 출력하지 않음). `--send-test`면 운영 경고 채널과 메일로 확인 메시지를 실제로 보낸다 |

## 처음 설정할 때

```bash
python -m app doctor              # 무엇이 빠졌는지 확인
python -m app doctor --send-test  # 슬랙 운영 채널과 메일이 실제로 도착하는지 확인
python -m app report --dry-run    # 발송 없이 리포트 파일만 만들어 보기
python -m app status              # 수집이 살아 있는지 확인
```

## 운영 DB (Supabase)

1. Supabase 프로젝트 → 상단 **Connect** → **Direct (Connection string)** 탭 → **Session pooler** 연결 문자열 복사.
   Direct connection 은 IPv6 전용이라 GitHub Actions 에서 접속되지 않는다.
2. `[YOUR-PASSWORD]` 를 DB 비밀번호로 바꾼다. 특수문자는 URL 인코딩(`@` → `%40`).
3. GitHub **Repository secrets** 에 `DATABASE_URL` 로 등록한다 (Variables·Environment secrets 가 아님).

테이블은 첫 연결 때 자동으로 만들어지고, 모든 테이블에 RLS 가 켜진다. 정책이 없으므로 Supabase REST API(공개 키)로는
읽고 쓸 수 없고, 연결 문자열을 가진 이 앱만 접근한다.

## 발송 리허설

수집기가 완성되기 전에 분류 → 리포트 → 슬랙·메일 발송을 실제 키로 확인한다.
GitHub **Actions → weekly-report → Run workflow** 에서 `rehearsal` 을 켜면 샘플 이벤트(제목에 `[샘플]`, 주차 `2020-W01`)를
넣어 실제 LLM 으로 분류·리포트하고 **실제로 발송**한 뒤 샘플과 그 리포트·발송 기록을 지운다. 실제 주차 데이터와는 섞이지 않는다.
로컬에서는 `python -m app seed` → `classify` → `report --week 2020-W01 --dry-run` → `seed --clear`.

## 운영 경고 (`SLACK_WEBHOOK_OPS` → `#monitor-ops`)

| 경고 | 뜻 |
|---|---|
| `수집 실패 <소스>/<브랜드>` | 수집기가 예외를 던짐 |
| `파싱 0건 <소스>/<브랜드>` | 예외는 없는데 저장된 항목이 0건 (site·sns만 검사. news는 새 기사만 저장하므로 0건이 정상) |
| `파싱 0건 <소스>/<브랜드> (<소스 이름>)` | 합계는 0이 아니지만, 전에는 수집되던 소스 하나가 이번에는 0건 (예: 유튜브는 들어오는데 인스타그램만 0건) |
| `주간 리포트 <주차>: 미분류 이벤트 N건` | 분류가 끝나기 전에 리포트가 만들어져 그 이벤트가 빠짐 |
| `LLM 분류 실패` | 분류 호출 실패. 이벤트는 미분류로 남아 다음 실행에서 다시 시도 |
| `주간 리포트 생성 실패` / `발송 실패 <주차>/<채널>` | 한 채널이 실패해도 다른 채널은 발송한다 |
| `긴급 알림 N건 발송 실패` | 중요도 5 알림이 리포트 채널로 나가지 못함. 다음 `classify` 실행에서 다시 시도 |

`SLACK_WEBHOOK_OPS`가 없으면 경고는 보내지 않고 콘솔에만 남는다.
슬랙 발송은 일시적인 실패(429, 5xx, 연결 오류)면 최대 3번까지 다시 시도한다. 주소가 틀린 경우(404 등)는 바로 실패한다.
경고와 로그의 오류 내용에서 주소(웹훅, API 키가 든 요청 주소, DB 주소)는 `[주소 가림]`으로 바뀐다.

## 운영 설정 (GitHub → Settings → Secrets and variables → Actions)

| 이름 | 종류 | 필수 | 용도 |
|---|---|---|---|
| `DATABASE_URL` | Secret | **필수** | PostgreSQL 주소 (`postgresql://user:pw@host:5432/db`). 없으면 러너의 SQLite를 쓰는데 실행이 끝나면 사라져서 데이터가 쌓이지 않는다 |
| `ANTHROPIC_API_KEY` | Secret | 필수 | 분류·리포트 LLM |
| `SLACK_WEBHOOK_REPORT` | Secret | 필수 | 주간 리포트·긴급 알림 채널 |
| `SLACK_WEBHOOK_OPS` | Secret | 권장 | 운영 경고 채널 |
| `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_TO` | Secret | 필수 | 메일 발송 (`MAIL_TO`는 쉼표로 여러 명). Gmail은 앱 비밀번호 사용 |
| `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `IG_ACCESS_TOKEN`, `IG_BUSINESS_ACCOUNT_ID`, `YOUTUBE_API_KEY` | Secret | B 수집기용 | 뉴스·SNS API |
| `SMTP_HOST`, `SMTP_PORT` | Variable | 선택 | 기본 `smtp.gmail.com` / `587`. 포트 465면 처음부터 TLS로 접속하고, 그 밖의 포트는 STARTTLS를 쓴다. 두 경우 모두 서버 인증서를 검증한다 |
| `MAIL_FROM_NAME` | Variable | 선택 | 보내는 사람 표시 이름. 기본 `경쟁사 모니터링` |
| `REPORT_URL` | Variable | 선택 | 슬랙 "전체 리포트 보기" 버튼 링크. 웹 대시보드를 배포했으면 `https://<배포 주소>/reports/latest` ([web/README.md](../../web/README.md)) |

## 자동 실행

- **daily-collect** (매일 06:00 KST): site → news → sns 수집 → 분류 → 스냅샷 정리. 한 단계가 실패해도 다음 단계는 실행된다.
- **weekly-report** (매주 월 08:00 KST): 리포트를 슬랙·메일로 발송한다. 저장소가 공개라서 리포트·대시보드 파일은 Actions 산출물로 올리지 않는다. 대시보드는 `DATABASE_URL`을 넣고 로컬에서 `python -m app dashboard`로 만든다.
- 둘 다 Actions 탭에서 **Run workflow**로 수동 실행할 수 있다.
- 두 워크플로는 같은 동시 실행 그룹(`monitor-pipeline`)이라 겹치지 않는다. daily가 늦게 끝나면 weekly는 기다렸다가 시작한다.
- 설치는 `constraints.txt`에 고정된 버전으로 한다. 의존성을 올릴 때는 새 가상환경에서 `pip install -e ".[dev]"` 후 `pip freeze --exclude-editable` 결과로 파일을 바꾸고 테스트를 돌린다.
- 두 워크플로는 `REQUIRE_DATABASE_URL=1`로 돈다. `DATABASE_URL` Secret이 없으면 임시 SQLite로 내려가지 않고 바로 실패하며 운영 경고를 보낸다.
- CLI는 실행할 때 시간대를 한국 시간으로 고정한다. 러너(UTC)에서 돌아도 수집·감지 시각과 주차 경계는 한국 시간 기준이다. (Windows는 PC의 시간대 설정을 따른다)

## 로컬에서 PostgreSQL로 테스트

```bash
TEST_DATABASE_URL=postgresql://postgres@localhost:5432/postgres pytest -q tests/infra
```

`TEST_DATABASE_URL`이 없으면 테스트는 임시 SQLite 파일을 쓴다. 지정한 DB의 테이블은 테스트마다 비워지므로 운영 DB를 넣지 말 것.
