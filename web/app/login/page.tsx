import { LoginForm } from "./form";

const ERRORS: Record<string, string> = {
  link: "로그인 링크가 만료됐거나 이미 사용됐습니다. 링크를 다시 받아 주세요.",
};

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; error?: string }>;
}) {
  const { next = "/", error } = await searchParams;
  return (
    <main className="login">
      <h1>경쟁사 모니터링</h1>
      <p className="muted">팀에 등록된 이메일로 로그인 링크를 받습니다.</p>
      {error && ERRORS[error] && <p className="notice error">{ERRORS[error]}</p>}
      <LoginForm next={next} />
    </main>
  );
}
