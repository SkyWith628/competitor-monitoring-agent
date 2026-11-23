# 🅳 web — 대시보드·주간 리포트 열람 (담당: D)

Supabase 에 쌓인 데이터를 팀원만 볼 수 있는 웹 페이지로 보여 준다. Next.js 앱이고 Vercel 에 배포한다.
수집·분류·발송은 여기서 하지 않는다 — 그건 GitHub Actions(`python -m app ...`)가 한다. 이 앱은 **읽기만** 한다.

| 주소 | 내용 |
|---|---|
| `/` | 운영 상태, 최근 30일 변화 추이, 중요도 4 이상 이벤트, 소스별 수집 현황, LLM 사용량 |
| `/reports` | 주간 리포트 목록 |
| `/reports/2026-W47` | 리포트 한 편 |
| `/reports/latest` | 가장 최근 리포트로 이동 — 슬랙 "전체 리포트 보기" 버튼(`REPORT_URL`)에 넣는 주소 |
| `/login` | 이메일로 로그인 링크 받기 (비밀번호 없음) |

## 누가 볼 수 있나

1. 로그인하지 않으면 모든 주소가 `/login` 으로 간다.
2. 로그인해도 `dashboard_members` 표에 이메일이 있어야 데이터가 보인다. 없으면 "접근 권한이 없습니다".
3. 이 제한은 화면이 아니라 DB(RLS 정책)가 건다. 앱은 로그인한 사용자의 권한으로만 조회하므로, 구성원이 아닌 사람은
   API 를 직접 불러도 0건이다. 구성원도 읽기만 가능하다.

## 처음 배포할 때 (한 번)

### 1. Supabase
1. **SQL Editor** 에 [`supabase/policies.sql`](supabase/policies.sql) 전체를 붙여 넣어 실행한다 (여러 번 실행해도 안전).
2. 같은 곳에서 팀원 이메일을 등록한다.
   ```sql
   insert into public.dashboard_members (email) values ('me@example.com'), ('teammate@example.com');
   ```
3. **Authentication → URL Configuration**: Site URL 을 Vercel 주소로, Redirect URLs 에 `https://<배포 주소>/auth/callback` 추가.
4. **Authentication → Sign In / Providers → Email**: "Confirm email" 은 켠 채로 둔다 (남의 이메일로 가입하는 것을 막는다).

### 2. Vercel
1. Vercel 에서 이 GitHub 저장소를 Import 하고 **Root Directory 를 `web`** 으로 지정한다.
2. Environment Variables 에 두 값을 넣는다 (Supabase → Connect → Framework 탭에 나온다).
   - `NEXT_PUBLIC_SUPABASE_URL`
   - `NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY`
3. Deploy. 이후 `main` 에 머지될 때마다 자동으로 다시 배포된다.

### 3. 슬랙 버튼 연결
GitHub 저장소 **Variables** 에 `REPORT_URL` = `https://<배포 주소>/reports/latest` 를 넣으면 주간 리포트 슬랙 메시지에
"전체 리포트 보기" 버튼이 생긴다.

### 4. (선택) 샘플로 화면 확인
수집기가 붙기 전에는 화면이 비어 있다. GitHub **Actions → sample-data → Run workflow → `seed-demo`** 로 예시 데이터를
넣어 볼 수 있다. 화면 위에 안내 문구가 뜨고, 같은 워크플로의 `clear` 로 지운다 (`app/infra/README.md` 참고).

## 알아둘 점
- 두 환경변수는 브라우저에 노출되는 공개 값이다. 데이터를 지키는 것은 이 키가 아니라 위의 RLS 정책이다.
  **DB 주소(`DATABASE_URL`)나 secret 키는 Vercel 에 넣지 않는다.**
- Supabase 기본 메일 발송은 시간당 보낼 수 있는 수가 매우 적다. 로그인 메일이 안 오면 잠시 후 다시 시도하거나,
  Supabase 에 자체 SMTP 를 연결한다.
- 로그인 링크는 **요청한 그 브라우저**에서 열어야 한다.
- 시각은 모두 한국 시간이다. DB 의 문자열을 그대로 보여 주므로 보는 사람의 시간대와 무관하다.
- 파이썬 쪽 `python -m app dashboard`(단일 HTML 파일)는 그대로 있다. 오프라인·메일 첨부용이다.

## 로컬에서 실행
```bash
cd web
cp .env.example .env.local   # 두 값 채우기
npm install
npm run dev                  # http://localhost:3000 (Supabase Redirect URLs 에 http://localhost:3000/auth/callback 추가)
npm run lint && npm run build
```

## 구조
```
proxy.ts                 요청마다 세션 갱신 + 미로그인 차단
lib/supabase/            서버용 클라이언트, 세션 처리
lib/data.ts              조회 함수 (서버 전용). JSON 은 DB 에 텍스트로 들어 있어 여기서 푼다
lib/format.ts            브랜드·변화 유형 라벨, 시각 표시
app/(app)/               로그인 후 화면 (대시보드, 리포트)
app/login, app/auth/     로그인 링크 요청, 링크 처리, 로그아웃
components/TrendChart    30일 추이 차트 (SVG, 키보드·표 보기 지원)
supabase/policies.sql    접근 권한(RLS 정책, 구성원 표, 집계 뷰)
```
