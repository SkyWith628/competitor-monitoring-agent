"use client";

import { useActionState } from "react";

import { sendLoginLink, type LoginState } from "./actions";

export function LoginForm({ next }: { next: string }) {
  const [state, action, pending] = useActionState<LoginState, FormData>(sendLoginLink, null);
  return (
    <form action={action} className="login-form">
      <input type="hidden" name="next" value={next} />
      <label htmlFor="email">이메일</label>
      <input id="email" name="email" type="email" autoComplete="email" required placeholder="you@example.com" />
      <button type="submit" disabled={pending}>
        {pending ? "보내는 중…" : "로그인 링크 받기"}
      </button>
      <p role="status" className={state ? (state.ok ? "notice ok" : "notice error") : "notice"}>
        {state?.message}
      </p>
    </form>
  );
}
