"""[D] 웹 대시보드 접근 권한(web/supabase/policies.sql) 테스트.

PostgreSQL 에서만 돈다 (CI 의 test-postgres 작업, 또는 TEST_DATABASE_URL 을 준 로컬 실행).
Supabase 가 제공하는 것 중 정책이 기대는 부분만 흉내 낸다:
  - 역할 anon(로그인 전) · authenticated(로그인 후)
  - auth.users 표와 auth.uid() — 로그인한 사용자의 id 는 요청의 JWT(request.jwt.claims)에서 온다
  - public 스키마의 새 테이블에 두 역할의 모든 권한이 기본으로 붙는 설정
"""
import json
import os
from pathlib import Path

import pytest
from psycopg import Error as DBAPIError

from app.core.models import Brand, Category, ChangeEvent, ChangeType, RawItem, SourceType, WeeklyReport
from app.infra.storage import db, repo

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="PostgreSQL 전용 (TEST_DATABASE_URL 필요)")

POLICIES = (Path(__file__).parents[2] / "web" / "supabase" / "policies.sql").read_text(encoding="utf-8")
READABLE = ["raw_items", "change_events", "reports", "report_sends", "llm_calls", "dashboard_source_status",
            "dashboard_llm_usage", "dashboard_daily_events", "dashboard_backlog", "dashboard_events"]
HIDDEN = ["snapshots", "urgent_alerts", "dashboard_members"]

MEMBER, UNCONFIRMED, OUTSIDER = (f"00000000-0000-0000-0000-00000000000{n}" for n in (1, 2, 3))

SUPABASE_LIKE = """
do $$ begin
  if not exists (select from pg_roles where rolname = 'anon') then create role anon nologin; end if;
  if not exists (select from pg_roles where rolname = 'authenticated') then create role authenticated nologin; end if;
end $$;
create schema if not exists auth;
create table if not exists auth.users (id uuid primary key, email text, email_confirmed_at timestamptz);
create or replace function auth.uid() returns uuid language sql stable as $f$
  select (nullif(current_setting('request.jwt.claims', true), '')::json ->> 'sub')::uuid
$f$;
grant usage on schema public, auth to anon, authenticated;
grant all on all tables in schema public to anon, authenticated;
"""


def _driver(conn):
    """SQLAlchemy 를 거치지 않는 psycopg 연결. 정책 SQL 의 format('%I') 를 자리표시자로 오해하지 않게 한다."""
    return conn._conn.connection.driver_connection


def _run(sql: str) -> None:
    with db.connect() as conn:
        _driver(conn).execute(sql)


@pytest.fixture
def secured(tmp_db):
    """앱 테이블 + Supabase 흉내 + 정책 적용(두 번: 다시 실행해도 안전해야 한다) + 데이터."""
    db.init_db()
    _run(SUPABASE_LIKE)
    _run(POLICIES)
    _run(POLICIES)
    _run("truncate public.dashboard_members; truncate auth.users;")
    _run(f"""
        insert into public.dashboard_members (email) values ('Member@Example.com'), ('victim@example.com');
        insert into auth.users (id, email, email_confirmed_at) values
          ('{MEMBER}', 'member@example.com', now()),
          ('{UNCONFIRMED}', 'victim@example.com', null),   -- 구성원 주소로 가입만 하고 메일 확인은 못 한 계정
          ('{OUTSIDER}', 'stranger@example.com', now());
    """)
    repo.save_items([RawItem(brand=Brand.SPAO, source_type=SourceType.SITE_PRODUCT, source="spao.com",
                             external_id="p1", url="https://spao.com/p1", title="티셔츠")])
    events = [ChangeEvent(brand=Brand.SPAO, change_type=ChangeType.PRICE_DOWN, title=f"이벤트 {n}",
                          url=f"https://spao.com/{n}", detected_at=f"2026-11-2{n}T09:00:00") for n in range(3)]
    repo.save_events(events)
    events[0].category, events[0].importance = Category.PRICING, 5
    events[1].category, events[1].importance = Category.IRRELEVANT, 4
    repo.update_events(events[:2])
    repo.save_report(WeeklyReport(week="2026-W47", tldr="요약", top_events=[], brand_summaries=[], insights=[],
                                  actions=[]))
    repo.mark_report_sent("2026-W47", "slack")
    repo.mark_urgent_sent([1])
    repo.save_llm_call("claude-haiku-5-5", 100, 20)


def _as(role: str, user: str | None, sql: str):
    """Supabase API 가 하듯, 그 역할과 로그인 사용자로 한 문장을 실행한 결과."""
    claims = json.dumps({"sub": user, "role": role, "email": "member@example.com"}) if user else ""
    with db.connect() as conn:
        raw = _driver(conn)
        raw.execute(f"set local role {role}")
        raw.execute(f"set local request.jwt.claims = '{claims}'")
        cursor = raw.execute(sql)
        return cursor.fetchall() if cursor.description else []


def _count(role: str, user: str | None, table: str) -> int:
    return _as(role, user, f"select count(*) from public.{table}")[0][0]


def test_confirmed_member_can_read_everything_the_dashboard_shows(secured):
    for table in READABLE:
        assert _count("authenticated", MEMBER, table) >= 1, table
    assert _as("authenticated", MEMBER, "select public.is_dashboard_member()")[0][0] is True


def test_non_member_and_unconfirmed_account_see_nothing(secured):
    # JWT 의 email 클레임은 구성원 주소로 적혀 있어도(_as 가 그렇게 넣는다) auth.users 의 실제 값으로 판정한다
    for user in (OUTSIDER, UNCONFIRMED):
        assert _as("authenticated", user, "select public.is_dashboard_member()")[0][0] is False
        for table in READABLE:
            if table == "dashboard_backlog":  # 합계 한 줄짜리 뷰: 줄은 항상 하나고, 볼 수 없는 사람에게는 0 이다
                assert _as("authenticated", user, "select unclassified from public.dashboard_backlog")[0][0] == 0
                continue
            assert _count("authenticated", user, table) == 0, (user, table)


def test_anonymous_is_denied_outright(secured):
    for table in READABLE + HIDDEN:
        with pytest.raises(DBAPIError, match="permission denied"):
            _count("anon", None, table)


def test_member_cannot_write_or_see_hidden_tables(secured):
    for table in HIDDEN:
        with pytest.raises(DBAPIError, match="permission denied"):
            _count("authenticated", MEMBER, table)
    for sql in ("delete from public.change_events", "update public.reports set tldr = 'x'",
                "insert into public.llm_calls (model) values ('x')", "truncate public.raw_items",
                "truncate public.snapshots", "insert into public.dashboard_members (email) values ('me@x.com')"):
        with pytest.raises(DBAPIError, match="permission denied"):
            _as("authenticated", MEMBER, sql)
    with pytest.raises(DBAPIError, match="permission denied"):
        _as("anon", None, "truncate public.urgent_alerts")


def test_aggregate_views_count_in_the_database(secured):
    daily = _as("authenticated", MEMBER, "select day, brand, events from public.dashboard_daily_events order by day")
    assert [tuple(r) for r in daily] == [("2026-11-20", "spao", 1), ("2026-11-21", "spao", 1),
                                         ("2026-11-22", "spao", 1)]
    assert _as("authenticated", MEMBER, "select unclassified from public.dashboard_backlog")[0][0] == 1
    important = _as("authenticated", MEMBER,
                    "select id from public.dashboard_events where importance >= 4 and category <> '무관'")
    assert [r[0] for r in important] == [1]  # 2번은 "무관", 3번은 미분류
    assert _as("authenticated", MEMBER,
               "select count(*) from public.dashboard_events where url like 'https://spao.com/%'")[0][0] == 3


def test_collector_still_works_after_policies(secured):
    """수집 앱은 테이블 소유자라 정책과 무관하게 읽고 쓴다."""
    assert repo.unclassified_count() == 1
    assert repo.report_channels_sent("2026-W47") == {"slack"}
    assert repo.prune_snapshots() == 0
