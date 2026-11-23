import type { NextConfig } from "next";

/*
 * 모든 응답에 붙는 보안 헤더.
 * 이 앱은 로그인한 팀원에게만 내부 데이터를 보여 주므로, 다른 사이트가 화면을 끼워 넣거나(클릭재킹)
 * 링크를 따라 나갈 때 주소가 새는 일을 막는다.
 *   - X-Frame-Options / frame-ancestors: 다른 사이트의 <iframe> 안에 이 화면을 띄울 수 없다.
 *   - X-Content-Type-Options: 브라우저가 파일 종류를 멋대로 추측해 실행하지 않는다.
 *   - Referrer-Policy: 수집한 기사·게시물 링크를 눌러도 상대 사이트에 이 대시보드의 주소를 보내지 않는다.
 *   - Permissions-Policy: 쓰지 않는 카메라·마이크·위치 권한을 꺼 둔다.
 *   - Strict-Transport-Security: 항상 HTTPS 로만 접속한다 (Vercel 도 붙여 주지만 다른 곳에 배포해도 유지되게).
 * 스크립트 출처를 제한하는 CSP(script-src)는 넣지 않았다. Next.js 의 인라인 스크립트 때문에 요청마다 nonce 를
 * 만들어야 해서, 필요해지면 proxy.ts 에서 따로 다룬다.
 */
const securityHeaders = [
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "Referrer-Policy", value: "no-referrer" },
  { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
  { key: "Strict-Transport-Security", value: "max-age=31536000" },
];

const nextConfig: NextConfig = {
  async headers() {
    return [{ source: "/(.*)", headers: securityHeaders }];
  },
};

export default nextConfig;
