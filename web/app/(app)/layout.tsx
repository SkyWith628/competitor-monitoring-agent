import Link from "next/link";
import { redirect } from "next/navigation";

import { isMember } from "@/lib/data";
import { createClient } from "@/lib/supabase/server";

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const supabase = await createClient();
  const { data } = await supabase.auth.getUser();
  if (!data.user) redirect("/login");
  const member = await isMember();

  return (
    <>
      <header className="topbar">
        <nav aria-label="주 메뉴">
          <Link href="/" className="brandmark">
            경쟁사 모니터링
          </Link>
          <Link href="/">대시보드</Link>
          <Link href="/reports">주간 리포트</Link>
        </nav>
        <form action="/auth/signout" method="post" className="account">
          <span className="muted">{data.user.email}</span>
          <button type="submit" className="linklike">
            로그아웃
          </button>
        </form>
      </header>
      <main className="page">
        {member ? (
          children
        ) : (
          <section className="card">
            <h1>접근 권한이 없습니다</h1>
            <p>
              <strong>{data.user.email}</strong> 은 대시보드 구성원으로 등록돼 있지 않습니다. 팀 관리자(D)에게 등록을
              요청해 주세요.
            </p>
          </section>
        )}
      </main>
    </>
  );
}
