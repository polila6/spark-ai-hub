export function bytes(n?: number | null, digits = 1): string {
  if (n === null || n === undefined || Number.isNaN(n)) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = n;
  while (v >= 1000 && i < u.length - 1) {
    v /= 1000;
    i++;
  }
  return `${v.toFixed(i === 0 ? 0 : digits)} ${u[i]}`;
}

export function rate(bps?: number | null): string {
  if (bps === null || bps === undefined) return "—";
  return `${bytes(bps)}/s`;
}

export function num(v?: number | null, digits = 1, suffix = ""): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "—";
  return `${v.toFixed(digits)}${suffix}`;
}

export function pct(v?: number | null): string {
  return num(v, 1, " %");
}

export function dt(v?: string | null, lang = "es"): string {
  if (!v) return "—";
  return new Date(v).toLocaleString(lang === "es" ? "es-ES" : "en-GB", { dateStyle: "short", timeStyle: "short" });
}

export function ago(v?: string | null, lang = "es"): string {
  if (!v) return "—";
  const s = (Date.now() - new Date(v).getTime()) / 1000;
  const rtf = new Intl.RelativeTimeFormat(lang, { numeric: "auto" });
  if (s < 60) return rtf.format(-Math.round(s), "second");
  if (s < 3600) return rtf.format(-Math.round(s / 60), "minute");
  if (s < 86400) return rtf.format(-Math.round(s / 3600), "hour");
  return rtf.format(-Math.round(s / 86400), "day");
}

export function duration(sec?: number | null): string {
  if (sec === null || sec === undefined) return "—";
  if (sec < 60) return `${sec.toFixed(1)} s`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m} min ${Math.round(sec % 60)} s`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

export function compact(n?: number | null): string {
  if (n === null || n === undefined) return "—";
  return Intl.NumberFormat("en", { notation: "compact" }).format(n);
}

export function params(b?: number | null): string {
  if (b === null || b === undefined) return "";
  if (b >= 1) return `${b.toFixed(1)}B`;
  const m = b * 1000;
  return m >= 1 ? `${m.toFixed(0)}M` : `${(m * 1000).toFixed(0)}K`;
}
