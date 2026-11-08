"use client";
// Building blocks of the three-zone Workspace layout: a panel header, the thin rails shown when a side panel is
// collapsed, the narrow-screen tab bar and a hook that tells how wide the layout container is.
import { PanelLeftOpen, PanelRightOpen } from "lucide-react";
import { useLayoutEffect, useState } from "react";
import { cx } from "@/components/ui";

/** Header strip shared by every panel: small icon + title on the left, actions on the right. */
export function PanelHeader({ icon, title, children, className }: { icon: React.ReactNode; title: React.ReactNode; children?: React.ReactNode; className?: string }) {
  return (
    <div className={cx("flex h-11 shrink-0 items-center gap-2 border-b border-border bg-elevated/40 pl-3 pr-1.5", className)}>
      <span className="text-muted [&>svg]:h-4 [&>svg]:w-4">{icon}</span>
      <span className="mr-auto min-w-0 truncate text-xs font-semibold tracking-wide text-fg">{title}</span>
      {children}
    </div>
  );
}

/** Width of an element, kept up to date (null until measured). Used to switch to the one-column layout. */
export function useWidth(ref: React.RefObject<HTMLElement | null>) {
  const [w, setW] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const on = () => setW(el.clientWidth);
    on();
    const ro = new ResizeObserver(on);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return w;
}

export type RailItem = { key: string; icon: React.ReactNode; label: string; onClick: () => void; badge?: number; spin?: boolean };

/** Thin vertical strip standing in for a collapsed side panel; every icon re-opens the panel. */
export function Rail({ side, expandLabel, onExpand, items = [], vertical }: {
  side: "left" | "right"; expandLabel: string; onExpand: () => void; items?: RailItem[]; vertical?: string;
}) {
  const btn = "relative grid h-8 w-8 place-items-center rounded-lg text-muted transition hover:bg-elevated hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40";
  return (
    <div className={cx("flex w-11 shrink-0 flex-col items-center gap-1 bg-elevated/30 py-2", side === "left" ? "border-r border-border" : "border-l border-border")}>
      <button type="button" onClick={onExpand} title={expandLabel} aria-label={expandLabel} className={btn}>
        {side === "left" ? <PanelLeftOpen className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />}
      </button>
      {items.map((it) => (
        <button key={it.key} type="button" onClick={it.onClick} title={it.label} aria-label={it.label} className={btn}>
          <span className={cx("[&>svg]:h-4 [&>svg]:w-4", it.spin && "text-info [&>svg]:animate-spin")}>{it.icon}</span>
          {!!it.badge && <span className="absolute -right-0.5 -top-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-accent px-1 text-[9px] font-bold text-accent-fg">{it.badge}</span>}
        </button>
      ))}
      {vertical && <span className="mt-2 max-h-[60%] select-none truncate text-[11px] text-subtle [writing-mode:vertical-rl]">{vertical}</span>}
    </div>
  );
}

export type TabDef<T extends string> = { id: T; label: string; icon: React.ReactNode; badge?: number };

/** Narrow screens: Files | Chat | Viewer as tabs instead of columns. */
export function ZoneTabs<T extends string>({ tabs, value, onChange, label }: { tabs: TabDef<T>[]; value: T; onChange: (v: T) => void; label: string }) {
  return (
    <div className="flex shrink-0 border-b border-border bg-elevated/40" role="tablist" aria-label={label}>
      {tabs.map((t) => (
        <button key={t.id} type="button" role="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}
          className={cx("relative flex flex-1 items-center justify-center gap-1.5 py-2.5 text-xs font-medium transition [&>svg]:h-4 [&>svg]:w-4",
            value === t.id ? "text-accent" : "text-muted hover:text-fg")}>
          {t.icon}{t.label}
          {!!t.badge && <span className="grid h-4 min-w-4 place-items-center rounded-full bg-accent px-1 text-[9px] font-bold text-accent-fg">{t.badge}</span>}
          {value === t.id && <span className="absolute inset-x-6 bottom-0 h-0.5 rounded-full bg-accent" />}
        </button>
      ))}
    </div>
  );
}
