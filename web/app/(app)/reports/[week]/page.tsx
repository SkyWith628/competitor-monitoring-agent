import Link from "next/link";
import { notFound } from "next/navigation";

import { eventLinks, getReport, reportChannels } from "@/lib/data";
import {
  CHANGE_LABEL,
  CHANNEL_LABEL,
  brandLabel,
  citedIds,
  formatTime,
  safeUrl,
  sendState,
  splitCitations,
} from "@/lib/format";

export const dynamic = "force-dynamic";

/** 문장 안의 근거 표시 [#12] 를 원문 링크로. 주소를 모르거나 http(s) 가 아니면 글자 그대로 둔다. */
function Cited({ text, links }: { text: string; links: Record<number, string> }) {
  return (
    <>
      {splitCitations(text).map((part, i) => {
        const url = part.id === undefined ? null : safeUrl(links[part.id]);
        return url ? (
          <a key={i} href={url} target="_blank" rel="noopener noreferrer">
            {part.text}
          </a>
        ) : (
          part.text
        );
      })}
    </>
  );
}

export default async function ReportPage({ params }: { params: Promise<{ week: string }> }) {
  const { week } = await params;
  if (!/^\d{4}-W\d{2}$/.test(week)) notFound();
  const [found, channels] = await Promise.all([getReport(week), reportChannels(week)]);
  if (!found) notFound();
  const { report, sent_at } = found;
  const links = await eventLinks(citedIds([...report.brand_summaries.flatMap((s) => s.highlights), ...report.insights]));
  const send = sendState(sent_at, channels);
  const label = (list: string[]) => list.map((c) => CHANNEL_LABEL[c] ?? c).join(", ");
  const sendLine =
    send.state === "unsent"
      ? "아직 발송되지 않은 초안입니다"
      : send.state === "sent"
        ? `발송 ${formatTime(sent_at)} · ${label(channels)}`
        : `일부만 발송됨${sent_at ? ` (${formatTime(sent_at)})` : ""}${channels.length ? ` · 발송: ${label(channels)}` : ""} · 미발송: ${label(send.missing)}`;

  return (
    <article className="report">
      <div className="page-head">
        <p>
          <Link href="/reports">← 주간 리포트</Link>
        </p>
        <h1>경쟁사 주간 리포트 {report.week}</h1>
        <p className="muted">
          {sendLine}
        </p>
      </div>

      <section className="card tldr">
        <h2>TL;DR</h2>
        <p>{report.tldr}</p>
      </section>

      <section className="card">
        <h2>Top 변화</h2>
        {report.top_events.length ? (
          <ol className="top">
            {report.top_events.map((e, i) => {
              const url = safeUrl(e.url);
              return (
                <li key={e.id ?? i}>
                  <span className="chip">{brandLabel(e.brand)}</span>{" "}
                  <span className="muted">{CHANGE_LABEL[e.change_type] ?? e.change_type}</span>
                  <div className="title">
                    {url ? (
                      <a href={url} target="_blank" rel="noopener noreferrer">
                        {e.title}
                      </a>
                    ) : (
                      e.title
                    )}
                  </div>
                  {e.llm_note && <div className="note">{e.llm_note}</div>}
                </li>
              );
            })}
          </ol>
        ) : (
          <p className="empty">이번 주 주요 변화가 없습니다.</p>
        )}
      </section>

      <section className="card">
        <h2>브랜드별 요약</h2>
        {report.brand_summaries.map((s) => (
          <div key={s.brand} className="brand-summary">
            <h3>{brandLabel(s.brand)}</h3>
            {s.highlights.length ? (
              <ul>
                {s.highlights.map((h, i) => (
                  <li key={i}>
                    <Cited text={h} links={links} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="empty">이번 주 주요 변화 없음</p>
            )}
          </div>
        ))}
      </section>

      <section className="card">
        <h2>인사이트</h2>
        <ul>
          {report.insights.map((t, i) => (
            <li key={i}>
              <Cited text={t} links={links} />
            </li>
          ))}
        </ul>
      </section>

      <section className="card">
        <h2>액션 제안</h2>
        <ul>
          {report.actions.map((t, i) => (
            <li key={i}>{t}</li>
          ))}
        </ul>
      </section>
    </article>
  );
}
