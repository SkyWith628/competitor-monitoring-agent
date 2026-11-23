import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "경쟁사 모니터링",
  description: "SPA 브랜드 경쟁사 모니터링 대시보드",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
