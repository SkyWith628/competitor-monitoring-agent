import Link from "next/link";

import { listReports } from "@/lib/data";
import { formatTime } from "@/lib/format";

export const dynamic = "force-dynamic";

export default async function ReportsPage() {
  const reports = await listReports();
  return (
    <>
      <div className="page-head">
        <h1>주간 리포트</h1>
      </div>
      <section className="card">
        {reports.length ? (
          <ul className="reports">
            {reports.map((r) => (
              <li key={r.week}>
                <Link href={`/reports/${r.week}`} className="week">
                  {r.week}
                </Link>
                <span className={r.sent_at ? "state good" : "state bad"}>
                  {r.sent_at ? `● 발송 ${formatTime(r.sent_at)}` : "▲ 미발송"}
                </span>
                <p>{r.tldr}</p>
              </li>
            ))}
          </ul>
        ) : (
          <p className="empty">아직 만들어진 리포트가 없습니다.</p>
        )}
      </section>
    </>
  );
}
