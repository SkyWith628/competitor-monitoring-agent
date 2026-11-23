import type { EmailOtpType } from "@supabase/supabase-js";
import { NextResponse, type NextRequest } from "next/server";

import { createClient } from "@/lib/supabase/server";

/** 메일의 로그인 링크가 돌아오는 곳. 링크의 일회용 값을 세션 쿠키로 바꾼다. */
export async function GET(request: NextRequest) {
  const params = request.nextUrl.searchParams;
  const next = params.get("next") ?? "/";
  const target = request.nextUrl.clone();
  target.search = "";
  target.pathname = next.startsWith("/") && !next.startsWith("//") ? next : "/";

  const supabase = await createClient();
  const code = params.get("code");
  const tokenHash = params.get("token_hash");
  const type = params.get("type") as EmailOtpType | null;
  let ok = false;
  if (code) {
    ok = !(await supabase.auth.exchangeCodeForSession(code)).error;
  } else if (tokenHash && type) {
    ok = !(await supabase.auth.verifyOtp({ type, token_hash: tokenHash })).error;
  }
  if (!ok) {
    target.pathname = "/login";
    target.search = "?error=link";
  }
  return NextResponse.redirect(target);
}
