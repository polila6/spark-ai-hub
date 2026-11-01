"use client";
import { ChevronDown, Lightbulb, LifeBuoy } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { GUIDES, type Bi, type GuideDef } from "@/lib/guides";
import { useI18n } from "@/lib/i18n";
import { cx } from "./ui";

const KEY = "sah-guides-closed";

function closedSet(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(KEY) || "[]"));
  } catch {
    return new Set();
  }
}

export function GuideBody({ g }: { g: GuideDef }) {
  const { lang } = useI18n();
  const L = (x: Bi) => (lang === "en" ? x.en : x.es);
  return (
    <div className="space-y-3 text-sm">
      <p className="text-muted">{L(g.intro)}</p>
      <ol className="space-y-2">
        {g.steps.map((s, i) => (
          <li key={i} className="flex gap-3">
            <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-accent/15 text-xs font-semibold text-accent">{i + 1}</span>
            <span className="pt-0.5 text-fg">{L(s)}</span>
          </li>
        ))}
      </ol>
      {g.tips?.map((tip, i) => (
        <p key={i} className="flex gap-2 rounded-lg bg-elevated/70 px-3 py-2 text-xs text-muted"><Lightbulb className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" />{L(tip)}</p>
      ))}
      {g.links && <div className="flex flex-wrap gap-2">{g.links.map((l) => <Link key={l.href} href={l.href} className="rounded-lg border border-border px-3 py-1.5 text-xs text-fg hover:bg-elevated">{L(l.label)} →</Link>)}</div>}
    </div>
  );
}

/** Collapsible "How does it work?" panel. Open on the first visit of each page; remembers when the user closes it. */
export function Guide({ id, className }: { id: keyof typeof GUIDES | string; className?: string }) {
  const { lang } = useI18n();
  const g = GUIDES[id];
  const [open, setOpen] = useState(false);
  useEffect(() => { setOpen(!closedSet().has(String(id))); }, [id]);
  if (!g) return null;
  function toggle() {
    const next = !open;
    setOpen(next);
    try {
      const s = closedSet();
      if (next) s.delete(String(id)); else s.add(String(id));
      localStorage.setItem(KEY, JSON.stringify([...s]));
    } catch {
      /* storage unavailable */
    }
  }
  return (
    <section className={cx("mb-5 rounded-2xl border border-accent/25 bg-accent/[0.04]", className)} aria-label={lang === "en" ? "Guide" : "Guía"}>
      <button type="button" onClick={toggle} aria-expanded={open} className="flex w-full items-center gap-2 px-4 py-2.5 text-left text-sm font-medium text-fg">
        <LifeBuoy className="h-4 w-4 text-accent" />
        <span>{lang === "en" ? "How does it work?" : "¿Cómo se usa?"}</span>
        <span className="text-xs font-normal text-muted">· {lang === "en" ? g.title.en : g.title.es}</span>
        <ChevronDown className={cx("ml-auto h-4 w-4 text-muted transition", open && "rotate-180")} />
      </button>
      {open && <div className="border-t border-accent/15 px-4 py-3"><GuideBody g={g} /></div>}
    </section>
  );
}

/** Empty state that explains what to do, in numbered steps. */
export function StepsEmpty({ icon, title, steps, action }: { icon?: React.ReactNode; title: string; steps: string[]; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-10 text-center">
      <div className="rounded-full bg-elevated p-3 text-subtle">{icon}</div>
      <p className="text-sm font-medium text-fg">{title}</p>
      <ol className="max-w-md space-y-1 text-left text-xs text-muted">{steps.map((s, i) => <li key={i}><span className="mr-1.5 font-semibold text-accent">{i + 1}.</span>{s}</li>)}</ol>
      {action}
    </div>
  );
}
