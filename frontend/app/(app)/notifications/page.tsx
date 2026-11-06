"use client";
import { Bell, CheckCheck, ChevronLeft, ChevronRight, MailOpen, Mail, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR, { useSWRConfig } from "swr";
import { BulkBar, BulkDeleteConfirm, SelectBox, useBulk, useSelection } from "@/components/bulk";
import { Guide } from "@/components/guide";
import { Button, Card, Confirm, Empty, PageHeader, Select, Skeleton, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useToast } from "@/lib/providers";

const PAGE = 50;

export default function NotificationsPage() {
  const L = useL();
  const { lang } = useI18n();
  const router = useRouter();
  const { push } = useToast();
  const { mutate: mutateAny } = useSWRConfig();
  const [filter, setFilter] = useState<"all" | "unread">("all");
  const [offset, setOffset] = useState(0);
  const { data, mutate } = useSWR(`/api/v1/notifications?limit=${PAGE}&offset=${offset}&filter=${filter}`, { refreshInterval: 30000 });
  const items: any[] = data?.items ?? [];
  const sel = useSelection(items.map((n) => String(n.id)));
  const [confirm, setConfirm] = useState<"selected" | "read" | null>(null);
  const refresh = () => { mutate(); mutateAny("/api/v1/notifications?limit=30"); };  // the bell menu shows the same data
  const bulk = useBulk("/api/v1/notifications/bulk", refresh);
  const ids = () => sel.list().map(Number);
  const total = data?.total ?? 0;
  const unread = data?.unread ?? 0;

  async function open(n: any) {
    if (!n.read) await api(`/api/v1/notifications/${n.id}/read`, { method: "POST" }).catch(() => {});
    refresh();
    if (n.link) { if (n.link.startsWith("/api/")) window.location.href = n.link; else router.push(n.link); }
  }
  async function simple(path: string, ok: string) {
    try { await api(path, { method: "POST" }); push("ok", ok); sel.clear(); refresh(); } catch (e: any) { push("error", e.message); }
  }
  const names = items.filter((n) => sel.has(String(n.id))).map((n) => n.title);
  return (
    <div>
      <PageHeader title={L("Notificaciones", "Notifications")} icon={<Bell className="h-5 w-5" />}
        subtitle={L(`${unread} sin leer · ${total} en esta vista`, `${unread} unread · ${total} in this view`)}
        actions={<>
          <Select className="w-40" aria-label={L("Filtro", "Filter")} value={filter} onChange={(e) => { setFilter(e.target.value as any); setOffset(0); sel.clear(); }}>
            <option value="all">{L("Todas", "All")}</option><option value="unread">{L("Sin leer", "Unread")}</option>
          </Select>
          <Button variant="outline" disabled={unread === 0} onClick={() => simple("/api/v1/notifications/read-all", L("Todas marcadas como leídas", "All marked as read"))}><CheckCheck className="h-4 w-4" />{L("Marcar todas como leídas", "Mark all as read")}</Button>
          <Button variant="outline" onClick={() => setConfirm("read")}><Trash2 className="h-4 w-4" />{L("Borrar leídas", "Delete read")}</Button>
        </>} />
      <Guide id="notifications" />
      <BulkBar count={sel.count} onClear={sel.clear} actions={[
        { key: "read", label: L("Marcar como leídas", "Mark as read"), icon: <MailOpen className="h-3.5 w-3.5" />, onClick: async () => { await bulk.run({ ids: ids(), action: "read" }, (n) => L(`${n} marcadas como leídas`, `${n} marked as read`)); sel.clear(); } },
        { key: "unread", label: L("Marcar como no leídas", "Mark as unread"), icon: <Mail className="h-3.5 w-3.5" />, onClick: async () => { await bulk.run({ ids: ids(), action: "unread" }, (n) => L(`${n} marcadas como no leídas`, `${n} marked as unread`)); sel.clear(); } },
        { key: "delete", label: L("Borrar", "Delete"), icon: <Trash2 className="h-3.5 w-3.5" />, danger: true, onClick: () => setConfirm("selected") },
      ]} />
      <Card>
        {!data ? <Skeleton className="h-40" /> : items.length === 0 ? <Empty title={filter === "unread" ? L("No tienes notificaciones sin leer", "No unread notifications") : L("No tienes notificaciones", "No notifications")} /> : (
          <>
            <div className="flex items-center gap-3 border-b border-border px-4 py-2 text-xs text-muted">
              <SelectBox checked={sel.all} indeterminate={sel.some} onChange={() => sel.toggleAll()} label={L("Seleccionar todas las de esta página", "Select all on this page")} />
              <span>{sel.count ? L(`${sel.count} seleccionadas`, `${sel.count} selected`) : L("Selecciona varias para marcarlas o borrarlas de una vez (Mayús para un rango)", "Select several to mark or delete them at once (Shift for a range)")}</span>
            </div>
            <ul className="divide-y divide-border/60">
              {items.map((n) => (
                <li key={n.id} className={cx("flex items-start gap-3 px-4 py-3", !n.read && "bg-accent/[0.06]", sel.has(String(n.id)) && "bg-accent/[0.12]")}>
                  <span className="mt-0.5"><SelectBox checked={sel.has(String(n.id))} onChange={(_, shift) => sel.toggle(String(n.id), shift)} label={L(`Seleccionar «${n.title}»`, `Select “${n.title}”`)} /></span>
                  <button type="button" className={cx("min-w-0 flex-1 text-left", n.link ? "cursor-pointer" : "cursor-default")} onClick={() => open(n)}>
                    <p className="flex items-center gap-2 text-sm text-fg">{!n.read && <span aria-label={L("sin leer", "unread")} className="h-1.5 w-1.5 shrink-0 rounded-full bg-accent" />}<span className={cx(!n.read && "font-medium")}>{n.title}</span></p>
                    {n.body && <p className="mt-0.5 line-clamp-3 text-xs text-muted">{n.body}</p>}
                    <p className="mt-1 text-[10px] text-subtle" title={new Date(n.created_at).toLocaleString()}>{ago(n.created_at, lang)}{n.link ? ` · ${L("abrir", "open")} →` : ""}</p>
                  </button>
                </li>
              ))}
            </ul>
            <div className="flex items-center justify-between border-t border-border px-4 py-2 text-xs text-muted">
              <span>{offset + 1}–{offset + items.length} / {total}</span>
              <span className="flex gap-1">
                <Button size="sm" variant="ghost" disabled={offset === 0} onClick={() => { setOffset(Math.max(0, offset - PAGE)); sel.clear(); }}><ChevronLeft className="h-3.5 w-3.5" />{L("Anteriores", "Newer")}</Button>
                <Button size="sm" variant="ghost" disabled={offset + PAGE >= total} onClick={() => { setOffset(offset + PAGE); sel.clear(); }}>{L("Siguientes", "Older")}<ChevronRight className="h-3.5 w-3.5" /></Button>
              </span>
            </div>
          </>
        )}
      </Card>
      <BulkDeleteConfirm open={confirm === "selected"} onOpenChange={(o) => !o && setConfirm(null)} names={names} noun={[L("notificación", "notification"), L("notificaciones", "notifications")]} busy={bulk.busy}
        onConfirm={async () => { await bulk.run({ ids: ids(), action: "delete" }, (n) => L(`${n} notificaciones borradas`, `${n} notifications deleted`)); sel.clear(); setConfirm(null); }} />
      <Confirm open={confirm === "read"} onOpenChange={(o) => !o && setConfirm(null)} danger title={L("Borrar las notificaciones leídas", "Delete read notifications")}
        text={L("Se borran todas las que ya has leído (también las de otras páginas). Las sin leer se conservan. No se puede deshacer.", "Every notification you have already read is deleted (on other pages too). Unread ones are kept. This cannot be undone.")}
        onConfirm={async () => { setConfirm(null); try { const r = await api("/api/v1/notifications/delete-read", { method: "POST" }); push("ok", L(`${r.done} notificaciones borradas`, `${r.done} notifications deleted`)); setOffset(0); sel.clear(); refresh(); } catch (e: any) { push("error", e.message); } }} />
    </div>
  );
}
