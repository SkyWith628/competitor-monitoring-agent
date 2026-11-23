"use server";

import { headers } from "next/headers";

import { createClient } from "@/lib/supabase/server";

export type LoginState = { ok: boolean; message: string } | null;

// 계정이 없는 주소로 요청했을 때 Supabase 가 돌려주는 오류 코드 (가입이 꺼져 있거나 shouldCreateUser: false)
const UNKNOWN_USER_CODES = new Set(["otp_disabled", "signup_disabled", "user_not_found"]);

/** 로그인 링크를 메일로 보낸다. 비밀번호는 없다. */
export async function sendLoginLink(_prev: LoginState, formData: FormData): Promise<LoginState> {
  const email = String(formData.get("email") ?? "").trim();
  const next = String(formData.get("next") ?? "/");
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return { ok: false, message: "이메일 주소를 확인해 주세요." };

  const h = await headers();
  const origin = h.get("origin") ?? `https://${h.get("host")}`;
  const callback = new URL("/auth/callback", origin);
  if (next.startsWith("/") && !next.startsWith("//")) callback.searchParams.set("next", next);

  const supabase = await createClient();
  // shouldCreateUser: false — 로그인 요청이 새 계정을 만들지 않는다. 계정은 관리자가 Supabase 에서 초대한
  // 사람에게만 있다(web/README.md). 아무 주소나 넣어 계정을 만들고 로그인 메일을 보내게 하는 일을 막는다.
  const { error } = await supabase.auth.signInWithOtp({
    email,
    options: { emailRedirectTo: callback.toString(), shouldCreateUser: false },
  });
  // 등록되지 않은 주소라는 사실은 알려 주지 않는다 (누가 구성원인지 떠볼 수 없게). 요청 과다와 그 밖의 장애만 구분한다.
  const unknownUser = error !== null && UNKNOWN_USER_CODES.has(error.code ?? "");
  if (error && !unknownUser) {
    const limited = error.status === 429;
    return {
      ok: false,
      message: limited ? "메일을 너무 자주 요청했습니다. 잠시 후 다시 시도해 주세요." : "메일을 보내지 못했습니다. 잠시 후 다시 시도해 주세요.",
    };
  }
  return {
    ok: true,
    message: `${email} 이(가) 등록된 주소라면 로그인 링크를 보냈습니다. 메일의 링크를 이 브라우저에서 열어 주세요.`,
  };
}
