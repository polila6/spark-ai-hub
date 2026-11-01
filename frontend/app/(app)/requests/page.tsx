"use client";
import { Check, Inbox, Search, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { Guide, StepsEmpty } from "@/components/guide";
import { Badge, Button, Card, ErrorBox, Field, Modal, PageHeader, Progress, Select, StatusBadge, Table, Td, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { bytes, dt } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { isManager, useMe, useToast } from "@/lib/providers";

export default function RequestsPage() {
  const L = useL();
  const { lang } = useI18n();
  const { me } = useMe();
  const { push } = useToast();
  const mgr = isManager(me);
  const { data, mutate } = useSWR("/api/v1/downloads", { refreshInterval: 8000 });
  const [rev, setRev] = useState<{ t: any; action: "approve" | "reject" } | null>(null);
  const [comment, setComment] = useState("");
  const [vis, setVis] = useState("public");
  const [err, setErr] = useState<any>(null);
  const pending = (data ?? []).filter((t: any) => t.awaiting_approval);
  const rest = (data ?? []).filter((t: any) => !t.awaiting_approval);
  const row = (t: any, actions: boolean) => (
    <tr key={t.id}>
      <Td><p className="font-medium">{t.repo_id}</p><p className="text-[11px] text-muted">{t.alias} · {t.backend}{t.license ? ` · ${t.license}` : ""}</p></Td>
      {mgr && <Td className="text-sm">{t.requested_by_name ?? "—"}</Td>}
      <Td className="max-w-[260px] text-xs text-muted">{t.request_reason || "—"}{t.review_comment && <p className="mt-1 text-fg">↳ {t.reviewed_by_name}: {t.review_comment}</p>}</Td>
      <Td className="text-xs">{bytes(t.total_bytes)}{t.estimated_memory_gb ? <p className="text-muted">{L("memoria", "memory")} ≈ {t.estimated_memory_gb} GB</p> : null}</Td>
      <Td>{t.awaiting_approval ? <Badge tone="warn">{L("pendiente", "pending")}</Badge> : t.status === "rejected" ? <Badge tone="danger">{L("rechazada", "rejected")}</Badge> : <StatusBadge state={t.status} />}
        {t.status === "running" && <Progress className="mt-1 w-28" value={(t.downloaded_bytes / Math.max(t.total_bytes, 1)) * 100} />}</Td>
      <Td className="whitespace-nowrap text-xs text-muted">{dt(t.created_at, lang)}</Td>
      <Td>{actions ? <div className="flex gap-1">
        <Button size="sm" variant="primary" onClick={() => { setRev({ t, action: "approve" }); setComment(""); setVis("public"); setErr(null); }}><Check className="h-3.5 w-3.5" />{L("Aprobar", "Approve")}</Button>
        <Button size="sm" variant="ghost" onClick={() => { setRev({ t, action: "reject" }); setComment(""); setErr(null); }}><X className="h-3.5 w-3.5" />{L("Rechazar", "Reject")}</Button></div>
        : t.status === "completed" && t.model_id ? <Link href={`/models/${t.model_id}`} className="text-xs text-accent">{L("Ver modelo", "View model")} →</Link> : null}</Td>
    </tr>
  );
  const head = [L("Modelo", "Model"), ...(mgr ? [L("Solicitante", "Requester")] : []), L("Motivo / respuesta", "Reason / reply"), L("Tamaño", "Size"), L("Estado", "Status"), L("Fecha", "Date"), ""];
  return (
    <div>
      <PageHeader title={L("Solicitudes de modelos", "Model requests")} subtitle={mgr ? L("Revisa lo que piden los investigadores", "Review what researchers ask for") : L("Los modelos que has pedido", "Models you requested")} icon={<Inbox className="h-5 w-5" />}
        actions={<Link href="/discover"><Button variant="primary"><Search className="h-4 w-4" />{L("Buscar un modelo", "Find a model")}</Button></Link>} />
      <Guide id="requests" />
      {mgr && <Card className="mb-4"><div className="border-b border-border px-5 py-3 text-sm font-semibold">{L("Pendientes de revisión", "Awaiting review")} ({pending.length})</div>
        {pending.length ? <Table head={head}>{pending.map((t: any) => row(t, true))}</Table> : <p className="px-5 py-6 text-sm text-muted">{L("No hay solicitudes pendientes.", "No pending requests.")}</p>}</Card>}
      <Card>
        <div className="border-b border-border px-5 py-3 text-sm font-semibold">{mgr ? L("Historial de descargas", "Download history") : L("Mis solicitudes", "My requests")}</div>
        {data && !(mgr ? rest : data).length ? (
          <StepsEmpty icon={<Inbox className="h-5 w-5" />} title={L("Sin solicitudes", "No requests")} steps={[L("Ve a Descubrir modelos y busca el que quieras.", "Go to Discover models and search for one."), L("Pulsa Instalar y explica para qué lo necesitas.", "Click Install and explain what you need it for."), L("Te avisaremos cuando se apruebe y esté listo.", "We will notify you when approved and ready.")]} />
        ) : <Table head={head}>{(mgr ? rest : data ?? []).map((t: any) => row(t, false))}</Table>}
      </Card>
      <Modal open={!!rev} onOpenChange={() => setRev(null)} title={rev?.action === "approve" ? L("Aprobar solicitud", "Approve request") : L("Rechazar solicitud", "Reject request")} description={rev?.t.repo_id}>
        <div className="space-y-3">
          {rev?.action === "approve" && <Field label={L("¿Quién podrá usarlo?", "Who can use it?")}><Select value={vis} onChange={(e) => setVis(e.target.value)}><option value="public">{L("Todos los usuarios (público)", "All users (public)")}</option><option value="private">{L("Solo quien lo pidió", "Only the requester")}</option></Select></Field>}
          <Field label={L("Comentario para el solicitante", "Comment for the requester")}><Textarea rows={3} value={comment} onChange={(e) => setComment(e.target.value)} /></Field>
          {err && <ErrorBox error={err} />}
          <div className="flex justify-end gap-2"><Button variant="ghost" onClick={() => setRev(null)}>{L("Cancelar", "Cancel")}</Button>
            <Button variant={rev?.action === "approve" ? "primary" : "danger"} onClick={async () => { setErr(null); try { await api(`/api/v1/downloads/${rev!.t.id}/${rev!.action}`, { method: "POST", json: { comment, visibility: rev!.action === "approve" ? vis : undefined } }); push("ok", rev!.action === "approve" ? L("Aprobada: la descarga ha empezado", "Approved: download started") : L("Rechazada", "Rejected")); setRev(null); mutate(); } catch (e) { setErr(e); } }}>{L("Confirmar", "Confirm")}</Button></div>
        </div>
      </Modal>
    </div>
  );
}
