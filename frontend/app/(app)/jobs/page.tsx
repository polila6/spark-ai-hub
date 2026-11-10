"use client";
import { Ban, ListChecks, RotateCcw, X } from "lucide-react";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import useSWR from "swr";
import { BulkBar, BulkDeleteConfirm, DeleteIcon, SelectBox, useBulk, useSelection } from "@/components/bulk";
import { Guide } from "@/components/guide";
import { Badge, Button, Card, CardBody, CardHeader, Empty, Modal, PageHeader, Progress, Select, Skeleton, StatusBadge, Switch, Table, Td } from "@/components/ui";
import { api } from "@/lib/api";
import { ago, bytes, dt, duration } from "@/lib/format";
import { useI18n, useL, tr } from "@/lib/i18n";
import { useMe, useToast } from "@/lib/providers";

function JobDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { t, lang } = useI18n();
  const { push } = useToast();
  const { data: j, mutate } = useSWR(`/api/v1/jobs/${id}`, { refreshInterval: 2000 });
  const { data: ev } = useSWR(`/api/v1/jobs/${id}/events`, { refreshInterval: 2000 });
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={j?.title || id} description={j ? `${j.type} · ${j.sched_class} · prioridad ${j.priority}` : ""} wide>
      {!j ? <Skeleton className="h-40" /> : (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge state={j.status} />{j.queue_position && <Badge>#{j.queue_position} en cola</Badge>}
            <span className="text-xs text-muted">intento {j.attempts}/{j.max_attempts}</span>
            <div className="ml-auto flex gap-2">
              {["queued", "preparing", "running"].includes(j.status) && <Button size="sm" variant="danger" onClick={async () => { await api(`/api/v1/jobs/${id}/cancel`, { method: "POST" }); mutate(); }}><X className="h-3.5 w-3.5" />{t("jobs.cancel")}</Button>}
              {["failed", "interrupted", "cancelled"].includes(j.status) && <Button size="sm" variant="outline" onClick={async () => { try { await api(`/api/v1/jobs/${id}/retry`, { method: "POST" }); push("ok", t("common.retry")); } catch (e: any) { push("error", e.message); } }}><RotateCcw className="h-3.5 w-3.5" />{t("common.retry")}</Button>}
            </div>
          </div>
          <Progress value={j.progress * 100} tone={j.status === "failed" ? "danger" : j.status === "completed" ? "ok" : "accent"} />
          {j.message && <p className="text-xs text-muted">{j.message}</p>}
          {j.error && <p className="rounded-lg bg-danger/10 p-3 text-xs text-danger">{j.error}</p>}
          <div className="grid gap-3 text-xs sm:grid-cols-3">
            <div className="rounded-lg bg-elevated p-3"><p className="text-muted">Tiempos</p><p>creado {dt(j.created_at, lang)}</p><p>inicio {dt(j.started_at, lang)}</p><p>fin {dt(j.finished_at, lang)}</p></div>
            <div className="rounded-lg bg-elevated p-3"><p className="text-muted">Recursos solicitados ({t("common.estimated")})</p><pre className="whitespace-pre-wrap font-mono text-[10px]">{JSON.stringify(j.requested_resources, null, 1)}</pre></div>
            <div className="rounded-lg bg-elevated p-3"><p className="text-muted">Recursos reales ({t("common.measured")})</p><pre className="whitespace-pre-wrap font-mono text-[10px]">{JSON.stringify(j.actual_resources, null, 1)}</pre></div>
          </div>
          {j.artifacts?.length > 0 && <div className="flex flex-wrap gap-2">{j.artifacts.map((a: any) => <a key={a.id} href={`${a.url}?download=true`}><Badge tone="accent">{a.filename} · {bytes(a.size_bytes)}</Badge></a>)}</div>}
          {j.result && <details><summary className="cursor-pointer text-xs text-muted">{t("common.result")}</summary><pre className="mt-2 max-h-60 overflow-auto rounded-lg bg-elevated p-3 font-mono text-[10px]">{JSON.stringify(j.result, null, 2)}</pre></details>}
          <div>
            <p className="mb-2 text-xs font-medium text-muted">{t("jobs.events")}</p>
            <ul className="max-h-64 space-y-1 overflow-y-auto font-mono text-[11px]">
              {ev?.map((e: any) => <li key={e.id} className={e.level === "error" ? "text-danger" : e.level === "warning" ? "text-warn" : "text-fg/80"}><span className="text-subtle">{new Date(e.ts).toLocaleTimeString()}</span> {e.message}
                {e.data?.remediation && <pre className="mt-1 whitespace-pre-wrap text-[10px] text-muted">{e.data.remediation}</pre>}</li>)}
            </ul>
          </div>
        </div>
      )}
    </Modal>
  );
}

function JobsInner() {
  const { t, lang } = useI18n();
  const { me } = useMe();
  const params = useSearchParams();
  const [status, setStatus] = useState("");
  const [all, setAll] = useState(false);
  const [open, setOpen] = useState<string | null>(params.get("open"));
  const { data, mutate } = useSWR(`/api/v1/jobs?limit=100${status ? `&status=${status}` : ""}${all ? "&all_users=true" : ""}`, { refreshInterval: 4000 });
  useEffect(() => {
    const es = new EventSource("/api/v1/jobs/stream/updates");
    es.onmessage = () => mutate();
    return () => es.close();
  }, [mutate]);
  // several jobs at once: cancel the running ones, delete the finished ones (the server skips what does not apply and says why)
  const L = useL();
  const ids = useMemo<string[]>(() => (data?.items ?? []).map((j: any) => j.id), [data]);
  const sel = useSelection(ids);
  const bulk = useBulk("/api/v1/jobs/bulk", () => { sel.clear(); mutate(); });
  const [confirmDelete, setConfirmDelete] = useState(false);
  const titleOf = (id: string) => { const j = data?.items?.find((x: any) => x.id === id); return j?.title || id.slice(0, 8); };
  return (
    <div>
      <PageHeader title={t("jobs.title")} subtitle={t("jobs.subtitle")} icon={<ListChecks className="h-5 w-5" />}
        actions={<><Select className="w-40" value={status} onChange={(e) => setStatus(e.target.value)}><option value="">{t("common.all")}</option>{["queued", "running", "completed", "failed", "cancelled", "interrupted"].map((s) => <option key={s} value={s}>{t(`state.${s}`)}</option>)}</Select>
          {me?.role === "admin" && <Switch checked={all} onChange={setAll} label={t("jobs.all_users")} />}</>} />
      <Guide id="jobs" />
      <BulkBar count={sel.count} onClear={sel.clear} noun={[L("trabajo seleccionado", "job selected"), L("trabajos seleccionados", "jobs selected")]} actions={[
        { key: "cancel", label: L("Cancelar los que estén en marcha", "Cancel the running ones"), icon: <Ban className="h-3.5 w-3.5" />, disabled: bulk.busy,
          onClick: () => bulk.run({ ids: sel.list(), action: "cancel" }, (n) => L(`${n} cancelados`, `${n} cancelled`)) },
        { key: "delete", label: L("Borrar los terminados", "Delete the finished ones"), icon: DeleteIcon, danger: true, disabled: bulk.busy, onClick: () => setConfirmDelete(true) },
      ]} />
      <BulkDeleteConfirm open={confirmDelete} onOpenChange={setConfirmDelete} names={sel.list().map(titleOf)} noun={[L("trabajo", "job"), L("trabajos", "jobs")]} busy={bulk.busy}
        extra={L("Solo se borran los que ya terminaron; los archivos que generaron se conservan.", "Only finished jobs are deleted; the files they produced are kept.")}
        onConfirm={async () => { await bulk.run({ ids: sel.list(), action: "delete" }, (n) => L(`${n} borrados`, `${n} deleted`)); setConfirmDelete(false); }} />
      <Card>
        {!data ? <Skeleton className="h-40" /> : data.items.length === 0 ? <Empty /> : (
          <Table head={[<SelectBox key="all" checked={sel.all} indeterminate={sel.some} onChange={() => sel.toggleAll()} label={L("Seleccionar todos", "Select all")} />, t("common.name"), t("jobs.type"), t("jobs.class"), t("common.status"), t("jobs.progress"), t("common.created"), tr("duración", "duration")]} hide={[4, 7, 8]}>
            {data.items.map((j: any) => (
              <tr key={j.id} className={`cursor-pointer hover:bg-elevated/40 ${sel.has(j.id) ? "bg-accent/5" : ""}`} onClick={() => setOpen(j.id)}>
                <Td className="w-8"><SelectBox checked={sel.has(j.id)} onChange={(_, shift) => sel.toggle(j.id, shift)} label={L("Seleccionar este trabajo", "Select this job")} /></Td>
                <Td><span className="font-medium">{j.title || j.id.slice(0, 8)}</span>{j.message && <div className="max-w-sm truncate text-[11px] text-muted">{j.message}</div>}{j.error && <div className="max-w-sm truncate text-[11px] text-danger">{j.error}</div>}</Td>
                <Td><Badge>{j.type}</Badge></Td><Td className="text-xs text-muted">{j.sched_class}</Td>
                <Td><StatusBadge state={j.status} /></Td>
                <Td className="w-40"><Progress value={j.progress * 100} tone={j.status === "failed" ? "danger" : j.status === "completed" ? "ok" : "accent"} /></Td>
                <Td className="whitespace-nowrap text-xs text-muted">{ago(j.created_at, lang)}</Td>
                <Td className="text-xs tabular-nums">{j.started_at ? duration(((j.finished_at ? new Date(j.finished_at) : new Date()).getTime() - new Date(j.started_at).getTime()) / 1000) : "—"}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
      {open && <JobDetail id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

export default function JobsPage() {
  return <Suspense><JobsInner /></Suspense>;
}
