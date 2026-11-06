"use client";
// Select several items of a list and act on all of them (delete, mark as read, cancel…) without opening each one.
// useSelection keeps the chosen ids (shift-click selects a range); BulkBar is the bar that appears while something is selected;
// SelectBox is the checkbox; runBulk sends one request and tells the person what was done and what was skipped, and why.
import { Trash2, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button, Confirm, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { useL } from "@/lib/i18n";
import { useToast } from "@/lib/providers";

export type Selection = ReturnType<typeof useSelection>;

/** The ids chosen in a list. `ids` = every id currently shown (in order); ids that disappear from the list are dropped. */
export function useSelection(ids: string[]) {
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const last = useRef<string | null>(null);
  const shown = useMemo(() => new Set(ids), [ids]);
  useEffect(() => {
    setChosen((cur) => {
      const kept = new Set([...cur].filter((x) => shown.has(x)));
      return kept.size === cur.size ? cur : kept;
    });
  }, [shown]);
  const toggle = useCallback((id: string, range = false) => {
    setChosen((cur) => {
      const next = new Set(cur);
      if (range && last.current && shown.has(last.current)) {  // shift-click: everything between the last click and this one
        const a = ids.indexOf(last.current), b = ids.indexOf(id);
        const [from, to] = a < b ? [a, b] : [b, a];
        const on = !cur.has(id);
        for (const x of ids.slice(from, to + 1)) on ? next.add(x) : next.delete(x);
      } else next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
    last.current = id;
  }, [ids, shown]);
  const all = ids.length > 0 && ids.every((x) => chosen.has(x));
  return {
    chosen, count: chosen.size, has: (id: string) => chosen.has(id), toggle,
    all, some: chosen.size > 0 && !all,
    toggleAll: () => setChosen(all ? new Set() : new Set(ids)),
    clear: () => { setChosen(new Set()); last.current = null; },
    list: () => [...chosen],
  };
}

export function SelectBox({ checked, indeterminate, onChange, label }: { checked: boolean; indeterminate?: boolean; onChange: (e: React.MouseEvent | React.ChangeEvent, shift: boolean) => void; label: string }) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => { if (ref.current) ref.current.indeterminate = !!indeterminate; }, [indeterminate]);
  return (
    <input ref={ref} type="checkbox" aria-label={label} title={label} checked={checked}
      onClick={(e) => { e.stopPropagation(); onChange(e, e.shiftKey); }} onChange={() => {}}
      className={cx("h-4 w-4 shrink-0 cursor-pointer rounded border-border accent-accent")} />
  );
}

export type BulkAction = { key: string; label: string; icon?: React.ReactNode; danger?: boolean; onClick: () => void; disabled?: boolean };

/** The bar shown while something is selected: how many, the actions, and a way out. */
export function BulkBar({ count, actions, onClear, noun }: { count: number; actions: BulkAction[]; onClear: () => void; noun?: [string, string] }) {
  const L = useL();
  if (count === 0) return null;
  const [one, many] = noun ?? [L("seleccionado", "selected"), L("seleccionados", "selected")];
  return (
    <div role="toolbar" aria-label={L("Acciones sobre la selección", "Actions on the selection")}
      className="sticky top-2 z-20 mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-accent/40 bg-surface px-3 py-2 shadow-card">
      <span className="text-sm font-medium text-fg">{count} {count === 1 ? one : many}</span>
      <span className="ml-auto flex flex-wrap items-center gap-1.5">
        {actions.map((a) => (
          <Button key={a.key} size="sm" variant={a.danger ? "danger" : "outline"} disabled={a.disabled} onClick={a.onClick}>{a.icon}{a.label}</Button>
        ))}
        <Button size="sm" variant="ghost" onClick={onClear} aria-label={L("Quitar la selección", "Clear the selection")}><X className="h-3.5 w-3.5" />{L("Cancelar", "Cancel")}</Button>
      </span>
    </div>
  );
}

export type BulkReport = { done: number; skipped: { id: string; name?: string; reason: string }[] };

/** POST one bulk request and report the result: «3 borrados · 2 omitidos (motivo…)». */
export function useBulk(path: string, after: () => void) {
  const L = useL();
  const { push } = useToast();
  const [busy, setBusy] = useState(false);
  const run = useCallback(async (body: Record<string, any>, doneText: (n: number) => string): Promise<BulkReport | null> => {
    setBusy(true);
    try {
      const r: BulkReport = await api(path, { method: "POST", json: body });
      const skipped = r.skipped?.length ?? 0;
      const why = skipped ? ` — ${L("omitidos", "skipped")}: ` + r.skipped.slice(0, 3).map((s) => `${s.name ?? s.id.slice(0, 8)} (${s.reason})`).join("; ") + (skipped > 3 ? "…" : "") : "";
      push(skipped && !r.done ? "error" : "ok", `${doneText(r.done)}${why}`);
      after();
      return r;
    } catch (e: any) {
      push("error", e.message);
      return null;
    } finally { setBusy(false); }
  }, [path, after, push, L]);
  return { run, busy };
}

/** A «delete N» confirmation that names what will be deleted. */
export function BulkDeleteConfirm({ open, onOpenChange, names, noun, busy, onConfirm, extra }: {
  open: boolean; onOpenChange: (o: boolean) => void; names: string[]; noun: [string, string]; busy?: boolean; onConfirm: () => void; extra?: string;
}) {
  const L = useL();
  const [one, many] = noun;
  const shown = names.slice(0, 6).map((n) => `«${n.length > 48 ? n.slice(0, 47) + "…" : n}»`).join(", ");
  return (
    <Confirm open={open} onOpenChange={onOpenChange} danger busy={busy}
      title={L(`Borrar ${names.length} ${names.length === 1 ? one : many}`, `Delete ${names.length} ${names.length === 1 ? one : many}`)}
      text={`${shown}${names.length > 6 ? L(` y ${names.length - 6} más`, ` and ${names.length - 6} more`) : ""}. ${L("No se puede deshacer.", "This cannot be undone.")}${extra ? " " + extra : ""}`}
      onConfirm={onConfirm} />
  );
}

export const DeleteIcon = <Trash2 className="h-3.5 w-3.5" />;
