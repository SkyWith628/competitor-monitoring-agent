"use client";

import { useState } from "react";

import { BRANDS, brandLabel, num } from "@/lib/format";

export type TrendDay = { day: string; counts: Record<string, number> };

const W = 720;
const H = 240;
const PAD = { top: 12, right: 8, bottom: 26, left: 36 };
const GAP = 2; // 쌓인 조각 사이의 간격(배경색) — 테두리 대신 여백으로 구분한다

function niceTicks(peak: number): number[] {
  if (peak <= 4) return Array.from({ length: Math.max(peak, 1) + 1 }, (_, i) => i);
  const raw = peak / 4;
  const pow = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 5, 10].map((m) => m * pow).find((s) => s >= raw)!;
  const ticks: number[] = [];
  for (let v = 0; v < peak + step; v += step) ticks.push(v);
  return ticks;
}

/** 최근 30일 일별 변화 건수(브랜드별 누적 막대). 막대에 올리거나 좌우 화살표로 날짜를 옮기면 수치가 보인다. */
export function TrendChart({ days }: { days: TrendDay[] }) {
  const [active, setActive] = useState<number | null>(null);
  const totals = days.map((d) => BRANDS.reduce((sum, b) => sum + (d.counts[b] ?? 0), 0));
  const ticks = niceTicks(Math.max(...totals, 0));
  const top = ticks[ticks.length - 1] || 1;
  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const band = plotW / days.length;
  const barW = Math.min(24, Math.max(4, band - 4));
  const y = (v: number) => PAD.top + plotH - (v / top) * plotH;
  const shown = active === null ? null : days[active];

  function onKey(e: React.KeyboardEvent) {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const step = e.key === "ArrowLeft" ? -1 : 1;
    setActive((i) => Math.min(days.length - 1, Math.max(0, (i ?? days.length - 1) + (i === null ? 0 : step))));
  }

  return (
    <figure className="chart">
      <ul className="legend" aria-label="범례">
        {BRANDS.map((b, i) => (
          <li key={b}>
            <span className={`swatch s${i + 1}`} aria-hidden="true" />
            {brandLabel(b)}
          </li>
        ))}
      </ul>
      <div className="chart-plot">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label="최근 30일 일별 변화 건수. 좌우 화살표로 날짜를 옮길 수 있습니다."
          tabIndex={0}
          onKeyDown={onKey}
          onBlur={() => setActive(null)}
          onMouseLeave={() => setActive(null)}
        >
          {ticks.map((t) => (
            <g key={t}>
              <line className="grid" x1={PAD.left} x2={W - PAD.right} y1={y(t)} y2={y(t)} />
              <text className="tick" x={PAD.left - 6} y={y(t)} textAnchor="end" dominantBaseline="middle">
                {num(t)}
              </text>
            </g>
          ))}
          {days.map((d, i) => {
            const x = PAD.left + i * band + (band - barW) / 2;
            const present = BRANDS.filter((b) => (d.counts[b] ?? 0) > 0);
            let acc = 0;
            return (
              <g key={d.day} onMouseEnter={() => setActive(i)}>
                {active === i && <rect className="hoverband" x={PAD.left + i * band} y={PAD.top} width={band} height={plotH} />}
                {present.map((b, k) => {
                  const v = d.counts[b];
                  const y0 = y(acc);
                  const y1 = y(acc + v);
                  acc += v;
                  const isTop = k === present.length - 1;
                  const h = Math.max(1, y0 - y1 - (isTop ? 0 : GAP));
                  const r = isTop ? Math.min(4, h, barW / 2) : 0;
                  const yTop = y0 - h;
                  // 맨 위 조각만 위쪽 모서리를 둥글린다 (기준선 쪽은 직각)
                  const path = `M${x},${y0} V${yTop + r} Q${x},${yTop} ${x + r},${yTop} H${x + barW - r} Q${x + barW},${yTop} ${x + barW},${yTop + r} V${y0} Z`;
                  return <path key={b} className={`s${BRANDS.indexOf(b) + 1}`} d={path} />;
                })}
                {/* 막대보다 넓은 투명 영역으로 마우스를 받는다 */}
                <rect x={PAD.left + i * band} y={PAD.top} width={band} height={plotH} fill="transparent" />
                {(i % 5 === 0 || i === days.length - 1) && (
                  <text className="tick" x={x + barW / 2} y={H - 8} textAnchor="middle">
                    {d.day.slice(5).replace("-", "/")}
                  </text>
                )}
              </g>
            );
          })}
          <line className="axis" x1={PAD.left} x2={W - PAD.right} y1={y(0)} y2={y(0)} />
        </svg>
        {shown && active !== null && (
          <div
            className="tooltip"
            role="status"
            style={{ left: `${((PAD.left + (active + 0.5) * band) / W) * 100}%` }}
            data-side={active > days.length / 2 ? "left" : "right"}
          >
            <strong>{shown.day}</strong>
            <span className="muted">합계 {num(totals[active])}건</span>
            {BRANDS.map((b, i) => (
              <span key={b} className="row">
                <span className={`swatch s${i + 1}`} aria-hidden="true" />
                {brandLabel(b)} <b>{num(shown.counts[b] ?? 0)}</b>
              </span>
            ))}
          </div>
        )}
      </div>
      <details>
        <summary>표로 보기</summary>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">날짜</th>
                {BRANDS.map((b) => (
                  <th scope="col" key={b} className="n">
                    {brandLabel(b)}
                  </th>
                ))}
                <th scope="col" className="n">
                  합계
                </th>
              </tr>
            </thead>
            <tbody>
              {days.map((d, i) => (
                <tr key={d.day}>
                  <th scope="row">{d.day}</th>
                  {BRANDS.map((b) => (
                    <td key={b} className="n">
                      {num(d.counts[b] ?? 0)}
                    </td>
                  ))}
                  <td className="n">{num(totals[i])}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </details>
    </figure>
  );
}
