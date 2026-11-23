import "server-only";

import { createClient } from "@/lib/supabase/server";

import { lastDays, nowKst } from "./format";

export type ChangeEvent = {
  id: number;
  brand: string;
  change_type: string;
  title: string;
  detail: Record<string, unknown>;
  url: string;
  detected_at: string;
  category: string | null;
  importance: number | null;
  llm_note: string | null;
};

export type WeeklyReport = {
  week: string;
  tldr: string;
  top_events: ChangeEvent[];
  brand_summaries: { brand: string; highlights: string[]; event_ids: number[] }[];
  insights: string[];
  actions: string[];
  stats: Record<string, unknown>;
};

export type SourceStatus = {
  brand: string;
  source: string;
  source_type: string;
  items: number;
  last_collected_at: string | null;
};

export type LlmUsage = { model: string; calls: number; input_tokens: number; output_tokens: number };
export type ReportRow = { week: string; tldr: string | null; sent_at: string | null; channels: string[] };
export type DailyCount = { day: string; brand: string; events: number };

/** JSON 이 텍스트 컬럼에 들어 있다. 문법이 깨진 줄은 null. */
function parse(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null && !Array.isArray(v);
const str = (v: unknown): string | null => (typeof v === "string" ? v : null);
const strings = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);

/*
 * 이벤트 한 건을 화면이 기대하는 모양으로 맞춘다. 꼭 있어야 하는 값(브랜드·종류·제목·시각)이 없으면 null.
 * 문법은 맞지만 내용이 빈 줄("{}" 등) 하나 때문에 화면 전체가 500 이 되는 일을 막는다.
 */
function toEvent(value: unknown, id?: number): ChangeEvent | null {
  if (!isObject(value)) return null;
  const brand = str(value.brand);
  const change_type = str(value.change_type);
  const title = str(value.title);
  const detected_at = str(value.detected_at);
  if (!brand || !change_type || !title || !detected_at) return null;
  return {
    id: id ?? (typeof value.id === "number" ? value.id : 0),
    brand,
    change_type,
    title,
    detail: isObject(value.detail) ? value.detail : {},
    url: str(value.url) ?? "",
    detected_at,
    category: str(value.category),
    importance: typeof value.importance === "number" ? value.importance : null,
    llm_note: str(value.llm_note),
  };
}

/** 리포트도 같은 방식으로: 빠진 목록은 빈 목록으로 채워, 일부가 비어 있어도 나머지는 보여 준다. */
function toReport(value: unknown): WeeklyReport | null {
  if (!isObject(value)) return null;
  const week = str(value.week);
  if (!week) return null;
  const events = Array.isArray(value.top_events) ? value.top_events : [];
  const summaries = Array.isArray(value.brand_summaries) ? value.brand_summaries : [];
  return {
    week,
    tldr: str(value.tldr) ?? "",
    top_events: events.map((e) => toEvent(e)).filter((e): e is ChangeEvent => e !== null),
    brand_summaries: summaries.filter(isObject).map((s) => ({
      brand: str(s.brand) ?? "",
      highlights: strings(s.highlights),
      event_ids: Array.isArray(s.event_ids) ? s.event_ids.filter((n): n is number => typeof n === "number") : [],
    })),
    insights: strings(value.insights),
    actions: strings(value.actions),
    stats: isObject(value.stats) ? value.stats : {},
  };
}

function check<T>(result: { data: T | null; error: { message: string } | null }, what: string): T {
  if (result.error) throw new Error(`${what} 조회 실패: ${result.error.message}`);
  return result.data as T;
}

export async function isMember(): Promise<boolean> {
  const supabase = await createClient();
  const { data, error } = await supabase.rpc("is_dashboard_member");
  return !error && data === true;
}

/*
 * 이벤트 집계는 DB 의 뷰(supabase/policies.sql)가 한다.
 * API 는 한 번에 돌려주는 행 수에 상한(기본 1,000행)이 있어서, 이벤트를 전부 가져와 여기서 세면
 * 데이터가 쌓였을 때 합계와 차트가 조용히 잘린다.
 */

/** 최근 n일의 날짜·브랜드별 이벤트 수. (30일 × 브랜드 수 = 많아야 백여 행) */
export async function dailyEvents(days = 30): Promise<DailyCount[]> {
  const supabase = await createClient();
  const since = lastDays(days, nowKst())[0];
  return check(
    await supabase.from("dashboard_daily_events").select("day, brand, events").gte("day", since).limit(1000),
    "일별 이벤트 수",
  );
}

/** 분류를 기다리는 이벤트 수 (기간과 무관하게 전부). */
export async function unclassifiedCount(): Promise<number> {
  const supabase = await createClient();
  const row = check<{ unclassified: number } | null>(
    await supabase.from("dashboard_backlog").select("unclassified").maybeSingle(),
    "미분류 이벤트 수",
  );
  return row?.unclassified ?? 0;
}

/** 최근 n일의 중요도 4 이상 이벤트, 최신순. "무관"으로 분류된 것은 뺀다. */
export async function importantEvents(days = 30, limit = 20): Promise<ChangeEvent[]> {
  const supabase = await createClient();
  const since = lastDays(days, nowKst())[0];
  const rows = check(
    await supabase
      .from("dashboard_events")
      .select("id, data")
      .gte("detected_at", since)
      .gte("importance", 4)
      .neq("category", "무관")
      .order("detected_at", { ascending: false })
      .order("id", { ascending: false })
      .limit(limit),
    "주요 이벤트",
  );
  return rows.map((r) => toEvent(parse(r.data), r.id)).filter((e): e is ChangeEvent => e !== null);
}

/** 화면 확인용 샘플 이벤트가 들어 있는지 (app/infra/sample_data.py 가 넣는 주소 표식으로 찾는다). */
export async function hasSampleEvents(sampleUrlPrefix: string): Promise<boolean> {
  const supabase = await createClient();
  const rows = check(
    await supabase.from("dashboard_events").select("id").like("url", `${sampleUrlPrefix}%`).limit(1),
    "샘플 이벤트",
  );
  return rows.length > 0;
}

export async function sourceStatus(): Promise<SourceStatus[]> {
  const supabase = await createClient();
  return check(await supabase.from("dashboard_source_status").select("*").order("brand").order("source"), "수집 현황");
}

export async function llmUsage(): Promise<LlmUsage[]> {
  const supabase = await createClient();
  return check(await supabase.from("dashboard_llm_usage").select("*").order("model"), "LLM 사용량");
}

/** 주간 리포트 목록(최신 주차부터)과 각 주차가 끝까지 나간 채널. */
export async function listReports(limit = 52): Promise<ReportRow[]> {
  const supabase = await createClient();
  const rows = check(
    await supabase.from("reports").select("week, tldr, sent_at").order("week", { ascending: false }).limit(limit),
    "리포트 목록",
  );
  if (rows.length === 0) return [];
  const sends = check(
    await supabase
      .from("report_sends")
      .select("week, channel")
      .in("week", rows.map((r) => r.week))
      .limit(1000),
    "발송 기록",
  );
  const channels = new Map<string, string[]>();
  for (const s of sends) {
    if (s.channel.includes("#")) continue; // "slack#0" 같은 메시지 단위 기록은 채널 완료가 아니다
    channels.set(s.week, [...(channels.get(s.week) ?? []), s.channel]);
  }
  return rows.map((r) => ({ ...r, channels: channels.get(r.week) ?? [] }));
}

export async function getReport(week: string): Promise<{ report: WeeklyReport; sent_at: string | null } | null> {
  const supabase = await createClient();
  const row = check<{ data: string; sent_at: string | null } | null>(
    await supabase.from("reports").select("data, sent_at").eq("week", week).maybeSingle(),
    "리포트",
  );
  if (!row) return null;
  const report = toReport(parse(row.data));
  return report ? { report, sent_at: row.sent_at } : null;
}

/** 이 주차 리포트가 끝까지 나간 채널 이름들. */
export async function reportChannels(week: string): Promise<string[]> {
  const supabase = await createClient();
  const rows = check(await supabase.from("report_sends").select("channel").eq("week", week), "발송 기록");
  return rows.map((r) => r.channel).filter((c) => !c.includes("#"));
}
