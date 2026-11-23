import { NextResponse, type NextRequest } from "next/server";

import { listReports } from "@/lib/data";

/** 슬랙의 "전체 리포트 보기" 버튼(REPORT_URL)이 가리키는 고정 주소 → 가장 최근 리포트로 보낸다. */
export async function GET(request: NextRequest) {
  const [latest] = await listReports(1).catch(() => []);
  return NextResponse.redirect(new URL(latest ? `/reports/${latest.week}` : "/reports", request.url));
}
