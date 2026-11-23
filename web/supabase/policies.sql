-- 대시보드(web/) 접근 권한. Supabase → SQL Editor 에 붙여 넣어 한 번 실행한다. 여러 번 실행해도 안전하다.
--
-- 구조
--   - 수집 앱(Python)은 DB 주소로 직접 접속하는 테이블 소유자라 RLS 와 무관하게 읽고 쓴다.
--   - 대시보드는 로그인한 사용자의 권한으로 Supabase API 를 통해 "읽기만" 한다.
--   - 로그인했더라도 dashboard_members 에 이메일이 있고, 그 이메일의 확인을 마친 사람만 읽을 수 있다.
--     (가입 여부가 아니라 이 목록으로 막는다. 가입 자체도 Supabase 설정에서 꺼 둔다 → web/README.md)
--
-- 팀원 추가:  insert into public.dashboard_members (email) values ('teammate@example.com');
-- 팀원 제거:  delete from public.dashboard_members where email = 'teammate@example.com';

create table if not exists public.dashboard_members (
  email text primary key,
  added_at timestamptz not null default now()
);
alter table public.dashboard_members enable row level security;  -- 정책 없음: API 로는 아무도 못 본다
revoke all on public.dashboard_members from anon, authenticated;

-- 구성원 판정: "이메일 확인을 마친" 로그인 사용자이고, 그 이메일이 구성원 표에 있어야 한다.
--   - 토큰(JWT)에 적힌 이메일을 그대로 믿지 않고 auth.users 의 현재 값을 본다.
--   - email_confirmed_at 이 비어 있으면(메일의 링크를 누르지 않은 가입) 구성원으로 치지 않는다.
--     그렇지 않으면 "Confirm email" 설정이 꺼져 있을 때 남의 주소로 먼저 가입해 들어올 수 있다.
create or replace function public.is_dashboard_member()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1
    from auth.users u
    join public.dashboard_members m on lower(m.email) = lower(u.email)
    where u.id = auth.uid()
      and u.email_confirmed_at is not null
  );
$$;
revoke all on function public.is_dashboard_member() from public, anon;
grant execute on function public.is_dashboard_member() to authenticated;

-- 권한은 "전부 거두고, 필요한 것만 준다".
-- Supabase 는 public 스키마의 새 테이블에 anon·authenticated 의 모든 권한을 기본으로 붙인다.
-- RLS 가 행을 막아 주지만 TRUNCATE 처럼 RLS 가 다루지 않는 권한도 있으므로 남겨 두지 않는다.
do $$
declare
  t text;
begin
  -- 대시보드가 읽지 않는 테이블: 아무 권한도 주지 않는다 (snapshots = 원본 이력, urgent_alerts = 발송 기록)
  foreach t in array array['snapshots', 'urgent_alerts'] loop
    if to_regclass(format('public.%I', t)) is not null then
      execute format('alter table public.%I enable row level security', t);
      execute format('revoke all on public.%I from anon, authenticated', t);
    end if;
  end loop;

  -- 대시보드가 읽는 테이블: 구성원에게 읽기만
  foreach t in array array['raw_items', 'change_events', 'reports', 'report_sends', 'llm_calls'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon, authenticated', t);
    execute format('grant select on public.%I to authenticated', t);
    execute format('drop policy if exists "dashboard members can read" on public.%I', t);
    execute format(
      'create policy "dashboard members can read" on public.%I for select to authenticated '
      'using (public.is_dashboard_member())', t);
  end loop;
end $$;

-- 집계용 뷰. security_invoker: 뷰를 읽는 사람의 권한(위 정책)이 그대로 적용된다.
create or replace view public.dashboard_source_status
with (security_invoker = true) as
  select brand, source, source_type, count(*)::int as items, max(collected_at) as last_collected_at
  from public.raw_items
  group by brand, source, source_type;

create or replace view public.dashboard_llm_usage
with (security_invoker = true) as
  select model, count(*)::int as calls,
         coalesce(sum(input_tokens), 0)::bigint as input_tokens,
         coalesce(sum(output_tokens), 0)::bigint as output_tokens,
         max(called_at) as last_called_at
  from public.llm_calls
  group by model;

-- 이벤트 집계. API 는 한 번에 돌려주는 행 수에 상한(기본 1,000행)이 있어서, 이벤트를 통째로 가져와
-- 화면에서 세면 데이터가 쌓였을 때 숫자가 조용히 잘린다. 그래서 세는 일은 DB 에서 한다.

-- 날짜·브랜드별 건수 (30일 추이 차트, "최근 30일 변화" 합계). detected_at 은 한국 시각 ISO 문자열이다.
create or replace view public.dashboard_daily_events
with (security_invoker = true) as
  select left(detected_at, 10) as day, brand, count(*)::int as events
  from public.change_events
  group by left(detected_at, 10), brand;

-- 분류를 기다리는 이벤트 수 (python -m app status 의 "미분류 이벤트"와 같은 값)
create or replace view public.dashboard_backlog
with (security_invoker = true) as
  select count(*)::int as unclassified
  from public.change_events
  where (data::json ->> 'category') is null;

-- 이벤트 목록 조회용. data(JSON 문자열) 안의 값을 컬럼으로 꺼내 두어 API 에서 걸러 낼 수 있게 한다.
create or replace view public.dashboard_events
with (security_invoker = true) as
  select id, brand, detected_at, data,
         (data::json ->> 'importance')::int as importance,
         data::json ->> 'category' as category,
         data::json ->> 'url' as url
  from public.change_events;

do $$
declare
  v text;
begin
  foreach v in array array['dashboard_source_status', 'dashboard_llm_usage', 'dashboard_daily_events',
                           'dashboard_backlog', 'dashboard_events'] loop
    execute format('revoke all on public.%I from anon, authenticated', v);
    execute format('grant select on public.%I to authenticated', v);
  end loop;
end $$;
