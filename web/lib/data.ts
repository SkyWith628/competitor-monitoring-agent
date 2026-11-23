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
export type ReportRow = { week: string; tldr: string | null; sent_at: string | null };

/** JSON 이 텍스트 컬럼에 들어 있다. 한 줄이 깨져도 화면 전체가 죽지 않게 건너뛴다. */
function parse<T>(text: string): T | null {
  try {
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
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

export async function recentEvents(days = 30): Promise<ChangeEvent[]> {
  const supabase = await createClient();
  const since = lastDays(days, nowKst())[0];
  const rows = check(
    await supabase
      .from("change_events")
      .select("id, data")
      .gte("detected_at", since)
      .order("id", { ascending: false })
      .limit(5000),
    "이벤트",
  );
  return rows
    .map((r) => {
      const e = parse<ChangeEvent>(r.data);
      return e ? { ...e, id: r.id } : null;
    })
    .filter((e): e is ChangeEvent => e !== null);
}

export async function sourceStatus(): Promise<SourceStatus[]> {
  const supabase = await createClient();
  return check(await supabase.from("dashboard_source_status").select("*").order("brand").order("source"), "수집 현황");
}

export async function llmUsage(): Promise<LlmUsage[]> {
  const supabase = await createClient();
  return check(await supabase.from("dashboard_llm_usage").select("*").order("model"), "LLM 사용량");
}

export async function listReports(limit = 52): Promise<ReportRow[]> {
  const supabase = await createClient();
  return check(
    await supabase.from("reports").select("week, tldr, sent_at").order("week", { ascending: false }).limit(limit),
    "리포트 목록",
  );
}

export async function getReport(week: string): Promise<{ report: WeeklyReport; sent_at: string | null } | null> {
  const supabase = await createClient();
  const row = check<{ data: string; sent_at: string | null } | null>(
    await supabase.from("reports").select("data, sent_at").eq("week", week).maybeSingle(),
    "리포트",
  );
  if (!row) return null;
  const report = parse<WeeklyReport>(row.data);
  return report ? { report, sent_at: row.sent_at } : null;
}

export async function reportChannels(week: string): Promise<string[]> {
  const supabase = await createClient();
  const rows = check(await supabase.from("report_sends").select("channel").eq("week", week), "발송 기록");
  return rows.map((r) => r.channel).filter((c) => !c.includes("#"));
}
