import Link from "next/link";

import { TrendChart, type TrendDay } from "@/components/TrendChart";
import {
  dailyEvents,
  hasSampleEvents,
  importantEvents,
  listReports,
  llmUsage,
  sourceStatus,
  unclassifiedCount,
} from "@/lib/data";
import {
  BRANDS,
  CHANGE_LABEL,
  SOURCE_TYPE_LABEL,
  agoText,
  brandLabel,
  formatTime,
  hoursBetween,
  lastDays,
  nowKst,
  num,
  safeUrl,
  sendState,
  sendStateView,
} from "@/lib/format";

export const dynamic = "force-dynamic";

const SAMPLE_URL = "https://example.com/sample/"; // app/infra/sample_data.py 의 표식과 같아야 한다
const STALE_HOURS = 36; // 하루 한 번 수집하므로 이보다 오래 소식이 없으면 멈춘 것으로 본다 (python -m app status 와 같은 기준)

export default async function DashboardPage() {
  const now = nowKst();
  // 건수는 DB 가 센 값을 받는다 (이벤트를 통째로 가져오면 API 의 행 수 상한에서 잘린다 → lib/data.ts)
  const [daily, important, unclassified, sampleEvents, sources, usage, reports] = await Promise.all([
    dailyEvents(30),
    importantEvents(30, 20),
    unclassifiedCount(),
    hasSampleEvents(SAMPLE_URL),
    sourceStatus(),
    llmUsage(),
    listReports(1),
  ]);

  const days = lastDays(30, now);
  const byDay = new Map<string, Record<string, number>>(days.map((d) => [d, {}]));
  let total = 0;
  for (const row of daily) {
    const counts = byDay.get(row.day);
    if (!counts) continue; // 미래 날짜 등 범위 밖
    counts[row.brand] = (counts[row.brand] ?? 0) + row.events;
    total += row.events;
  }
  const trend: TrendDay[] = days.map((day) => ({ day, counts: byDay.get(day)! }));

  const stale = sources.filter((s) => !s.last_collected_at || hoursBetween(s.last_collected_at, now) > STALE_HOURS);
  const latest = reports[0];
  const latestState = latest ? sendStateView(sendState(latest.sent_at, latest.channels)) : null;
  const hasSamples = sampleEvents || sources.some((s) => s.source.startsWith("sample-"));

  return (
    <>
      <div className="page-head">
        <h1>대시보드</h1>
        <p className="muted">기준 시각 {formatTime(now)}</p>
      </div>

      {hasSamples && (
        <p className="banner" role="note">
          <strong>샘플 데이터가 섞여 있습니다.</strong> 제목에 [샘플]이 붙은 항목과 <code>sample-</code> 소스는 화면
          확인용 예시입니다. 운영을 시작하기 전에 GitHub Actions 의 sample-data 워크플로에서 clear 를 실행하세요.
        </p>
      )}

      <section className="tiles" aria-label="운영 상태">
        <div className="tile">
          <span className="label">수집 소스</span>
          <span className="value">{num(sources.length)}</span>
          <span className={stale.length ? "state bad" : "state good"}>
            {sources.length === 0 ? "아직 수집 전" : stale.length ? `▲ ${stale.length}개 지연` : "● 모두 정상"}
          </span>
        </div>
        <div className="tile">
          <span className="label">최근 30일 변화</span>
          <span className="value">{num(total)}</span>
          <span className="state">건</span>
        </div>
        <div className="tile">
          <span className="label">미분류 이벤트</span>
          <span className="value">{num(unclassified)}</span>
          <span className={unclassified ? "state bad" : "state good"}>{unclassified ? "▲ 분류 대기" : "● 없음"}</span>
        </div>
        <div className="tile">
          <span className="label">최근 리포트</span>
          <span className="value">{latest ? <Link href={`/reports/${latest.week}`}>{latest.week}</Link> : "-"}</span>
          <span className={latestState ? latestState.className : "state"}>
            {latestState ? latestState.text : "아직 없음"}
          </span>
        </div>
      </section>

      <section className="card">
        <h2>일별 변화 추이</h2>
        <p className="muted">최근 30일, 감지된 변화 건수 (브랜드별)</p>
        {total ? <TrendChart days={trend} /> : <p className="empty">최근 30일 동안 감지된 변화가 없습니다.</p>}
      </section>

      <section className="card">
        <h2>주요 이벤트</h2>
        <p className="muted">최근 30일, 중요도 4 이상</p>
        {important.length ? (
          <ul className="events">
            {important.map((e) => {
              const url = safeUrl(e.url);
              return (
                <li key={e.id}>
                  <div className="meta">
                    <span className="chip">{brandLabel(e.brand)}</span>
                    <span>{CHANGE_LABEL[e.change_type] ?? e.change_type}</span>
                    <span>중요도 {e.importance}</span>
                    <span>{formatTime(e.detected_at)}</span>
                  </div>
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
          </ul>
        ) : (
          <p className="empty">중요도 4 이상으로 분류된 이벤트가 없습니다.</p>
        )}
      </section>

      <section className="card">
        <h2>수집 현황</h2>
        <p className="muted">소스별 누적 항목 수와 마지막 수집 시각. {STALE_HOURS}시간 넘게 소식이 없으면 지연으로 표시합니다.</p>
        {sources.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">브랜드</th>
                  <th scope="col">소스</th>
                  <th scope="col">종류</th>
                  <th scope="col" className="n">
                    항목
                  </th>
                  <th scope="col">마지막 수집</th>
                  <th scope="col">상태</th>
                </tr>
              </thead>
              <tbody>
                {[...sources]
                  .sort((a, b) => BRANDS.indexOf(a.brand as never) - BRANDS.indexOf(b.brand as never))
                  .map((s) => {
                    const late = !s.last_collected_at || hoursBetween(s.last_collected_at, now) > STALE_HOURS;
                    return (
                      <tr key={`${s.brand}/${s.source}/${s.source_type}`}>
                        <td>{brandLabel(s.brand)}</td>
                        <td>{s.source}</td>
                        <td>{SOURCE_TYPE_LABEL[s.source_type] ?? s.source_type}</td>
                        <td className="n">{num(s.items)}</td>
                        <td>
                          {formatTime(s.last_collected_at)}
                          {s.last_collected_at && <span className="muted"> ({agoText(s.last_collected_at, now)})</span>}
                        </td>
                        <td className={late ? "state bad" : "state good"}>{late ? "▲ 지연" : "● 정상"}</td>
                      </tr>
                    );
                  })}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty">아직 수집된 항목이 없습니다.</p>
        )}
      </section>

      <section className="card">
        <h2>LLM 사용량</h2>
        <p className="muted">모델별 누적 호출 수와 토큰</p>
        {usage.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th scope="col">모델</th>
                  <th scope="col" className="n">
                    호출
                  </th>
                  <th scope="col" className="n">
                    입력 토큰
                  </th>
                  <th scope="col" className="n">
                    출력 토큰
                  </th>
                </tr>
              </thead>
              <tbody>
                {usage.map((u) => (
                  <tr key={u.model}>
                    <td>{u.model}</td>
                    <td className="n">{num(u.calls)}</td>
                    <td className="n">{num(Number(u.input_tokens))}</td>
                    <td className="n">{num(Number(u.output_tokens))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="empty">기록된 LLM 호출이 없습니다.</p>
        )}
      </section>
    </>
  );
}
