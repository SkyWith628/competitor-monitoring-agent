export const BRANDS = ["spao", "8seconds", "musinsa_standard", "uniqlo"] as const;
export type Brand = (typeof BRANDS)[number];

export const BRAND_LABEL: Record<string, string> = {
  spao: "스파오",
  "8seconds": "에잇세컨즈",
  musinsa_standard: "무신사 스탠다드",
  uniqlo: "유니클로",
};

export const CHANGE_LABEL: Record<string, string> = {
  new_product: "신상품",
  price_down: "가격 인하",
  price_up: "가격 인상",
  sold_out: "품절",
  restock: "재입고",
  promo_changed: "프로모션 변경",
  news: "뉴스",
  sns_spike: "SNS 반응 급증",
  sns_new_post: "SNS 새 게시물",
};

export const SOURCE_TYPE_LABEL: Record<string, string> = {
  site_product: "사이트 상품",
  site_page: "사이트 페이지",
  news: "뉴스",
  sns_post: "SNS",
};

export const brandLabel = (brand: string) => BRAND_LABEL[brand] ?? brand;

/*
 * 시각 규칙: DB의 시각은 "한국 시각"을 적은 ISO 문자열이다(시간대 표기가 없거나 +09:00).
 * 브라우저·서버의 시간대에 휘둘리지 않도록 Date 로 해석하지 않고 문자열 앞부분을 그대로 쓴다.
 */
export function formatTime(iso: string | null | undefined): string {
  if (!iso || iso.length < 16) return "-";
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)} KST`;
}

export const dayOf = (iso: string) => iso.slice(0, 10);

/** 지금의 한국 시각을 DB와 같은 모양("YYYY-MM-DDTHH:MM:SS")으로. */
export function nowKst(): string {
  return new Date(Date.now() + 9 * 3600 * 1000).toISOString().slice(0, 19);
}

/** 한국 시각 ISO 문자열 두 개의 차이(시간). */
export function hoursBetween(earlier: string, later: string): number {
  const ms = (s: string) => Date.parse(`${s.slice(0, 19)}Z`);
  return (ms(later) - ms(earlier)) / 3600000;
}

export function agoText(iso: string, now: string): string {
  const hours = hoursBetween(iso, now);
  if (hours < 1) return "1시간 이내";
  if (hours < 48) return `${Math.floor(hours)}시간 전`;
  return `${Math.floor(hours / 24)}일 전`;
}

/** 오늘(KST)까지 최근 n일의 날짜 목록, 오래된 순. */
export function lastDays(n: number, now: string): string[] {
  const end = Date.parse(`${now.slice(0, 10)}T00:00:00Z`);
  return Array.from({ length: n }, (_, i) => new Date(end - (n - 1 - i) * 86400000).toISOString().slice(0, 10));
}

export const num = (n: number) => n.toLocaleString("ko-KR");

/** 수집한 글에 들어 있는 주소는 http(s)만 링크로 건다. */
export function safeUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:" ? u.toString() : null;
  } catch {
    return null;
  }
}
