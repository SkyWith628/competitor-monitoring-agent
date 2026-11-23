"use server";

import { headers } from "next/headers";

import { createClient } from "@/lib/supabase/server";

export type LoginState = { ok: boolean; message: string } | null;

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
  const { error } = await supabase.auth.signInWithOtp({ email, options: { emailRedirectTo: callback.toString() } });
  if (error) {
    const limited = error.status === 429;
    return {
      ok: false,
      message: limited ? "메일을 너무 자주 요청했습니다. 잠시 후 다시 시도해 주세요." : "메일을 보내지 못했습니다. 잠시 후 다시 시도해 주세요.",
    };
  }
  return { ok: true, message: `${email} 로 로그인 링크를 보냈습니다. 메일의 링크를 이 브라우저에서 열어 주세요.` };
}
