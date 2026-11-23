-- 대시보드(web/) 접근 권한. Supabase → SQL Editor 에 붙여 넣어 한 번 실행한다. 여러 번 실행해도 안전하다.
--
-- 구조
--   - 수집 앱(Python)은 DB 주소로 직접 접속하는 테이블 소유자라 RLS 와 무관하게 읽고 쓴다.
--   - 대시보드는 로그인한 사용자의 권한으로 Supabase API 를 통해 "읽기만" 한다.
--   - 로그인했더라도 dashboard_members 에 이메일이 있는 사람만 읽을 수 있다.
--     (Supabase 는 기본적으로 누구나 가입할 수 있으므로, 가입 여부가 아니라 이 목록으로 막는다)
--
-- 팀원 추가:  insert into public.dashboard_members (email) values ('teammate@example.com');
-- 팀원 제거:  delete from public.dashboard_members where email = 'teammate@example.com';

create table if not exists public.dashboard_members (
  email text primary key,
  added_at timestamptz not null default now()
);
alter table public.dashboard_members enable row level security;  -- 정책 없음: API 로는 아무도 못 본다
revoke all on public.dashboard_members from anon, authenticated;

create or replace function public.is_dashboard_member()
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from public.dashboard_members m
    where lower(m.email) = lower(auth.jwt() ->> 'email')
  );
$$;
revoke all on function public.is_dashboard_member() from public, anon;
grant execute on function public.is_dashboard_member() to authenticated;

-- 대시보드가 읽는 테이블만 연다. snapshots(원본 이력)·urgent_alerts 는 열지 않는다.
do $$
declare
  t text;
begin
  foreach t in array array['raw_items', 'change_events', 'reports', 'report_sends', 'llm_calls'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('revoke all on public.%I from anon', t);
    execute format('revoke insert, update, delete, truncate on public.%I from authenticated', t);
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

revoke all on public.dashboard_source_status, public.dashboard_llm_usage from anon;
grant select on public.dashboard_source_status, public.dashboard_llm_usage to authenticated;
