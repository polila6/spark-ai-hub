"use client";
import { Download, Lock, Users } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { Badge, Button, Card, CardHeader, Empty, Field, Modal, Notice, Skeleton, Table, Td, Textarea } from "@/components/ui";
import { api, postBlob } from "@/lib/api";
import { ago, bytes, compact, dt } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useToast } from "@/lib/providers";

const TONES: Record<string, "ok" | "accent" | "muted" | "warn"> = { online: "ok", active: "accent", inactive: "muted", never: "muted", disabled: "warn" };

function ContentAccess({ id, name, consent }: { id: string; name: string; consent: boolean }) {
  const L = useL();
  const { push } = useToast();
  const [reason, setReason] = useState("");
  const [index, setIndex] = useState<any>(null);
  const [chat, setChat] = useState<any>(null);
  const [space, setSpace] = useState<any>(null);
  const base = `/api/v1/admin/people/${id}`;
  const ready = reason.trim().length >= 10;
  const run = async (fn: () => Promise<void>) => { try { await fn(); } catch (e: any) { push("error", e?.message ?? String(e)); } };
  const save = (json: any, path: string, filename: string) => run(async () => {
    const url = URL.createObjectURL(await postBlob(path, json));
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  });
  if (!consent) return <Notice tone="muted" icon={<Lock className="h-4 w-4" />}>{L(`${name} aún no ha aceptado las normas nuevas, así que su contenido no se puede consultar.`, `${name} has not accepted the new terms yet, so their content cannot be consulted.`)}</Notice>;
  return (
    <div className="space-y-3 rounded-xl border border-border p-4">
      <h3 className="font-semibold">{L("Consultar su contenido", "Consult their content")}</h3>
      <Notice tone="warn">{L(`${name} verá en Ajustes → Privacidad cada consulta: tu nombre, cuándo, qué y este motivo.`, `${name} will see each consultation in Settings → Privacy: your name, when, what and this reason.`)}</Notice>
      <Field label={L("Motivo (mínimo 10 caracteres)", "Reason (at least 10 characters)")}><Textarea rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} /></Field>
      <Button disabled={!ready} onClick={() => run(async () => setIndex(await api(`${base}/content`, { method: "POST", json: { reason } })))}>{L("Ver sus chats y archivos", "See their chats and files")}</Button>
      {index && <div className="space-y-3">
        <details open><summary className="cursor-pointer font-semibold">{L(`Chats (${index.conversations.length})`, `Chats (${index.conversations.length})`)}</summary>
          <ul className="mt-2 space-y-1">{index.conversations.map((c: any) => <li key={c.id}><button className="text-left text-accent underline disabled:opacity-50" disabled={!ready} onClick={() => run(async () => setChat(await api(`${base}/conversations/${c.id}`, { method: "POST", json: { reason } })))}>{c.title || L("Sin título", "Untitled")}</button></li>)}</ul>
        </details>
        <details><summary className="cursor-pointer font-semibold">{L(`Archivos (${index.files.length})`, `Files (${index.files.length})`)}</summary>
          <ul className="mt-2 space-y-1">{index.files.map((f: any) => <li key={f.id} className="flex items-center gap-2"><Button size="sm" variant="ghost" disabled={!ready} aria-label={L("Descargar", "Download")} onClick={() => save({ reason }, `${base}/files/${f.id}`, f.filename)}><Download className="h-4 w-4" /></Button><span className="min-w-0 truncate">{f.filename}</span><span className="text-xs text-muted">{bytes(f.size_bytes)}</span></li>)}</ul>
        </details>
        <details><summary className="cursor-pointer font-semibold">{`Workspaces (${index.workspaces.length})`}</summary>
          <ul className="mt-2 space-y-1">{index.workspaces.map((w: any) => <li key={w.id}><button className="text-left text-accent underline disabled:opacity-50" disabled={!ready} onClick={() => run(async () => setSpace({ id: w.id, ...(await api(`${base}/workspaces/${w.id}`, { method: "POST", json: { reason } })) }))}>{w.name}</button> <span className="text-xs text-muted">{bytes(w.size_bytes)}</span></li>)}</ul>
          {space && <ul className="mt-2 max-h-64 space-y-1 overflow-y-auto">{space.tree.filter((e: any) => e.type === "file").map((e: any) => <li key={e.path} className="flex items-center gap-2"><Button size="sm" variant="ghost" disabled={!ready} aria-label={L("Descargar", "Download")} onClick={() => save({ reason, path: e.path }, `${base}/workspaces/${space.id}/file`, e.path.split("/").pop())}><Download className="h-4 w-4" /></Button><span className="min-w-0 truncate">{e.path}</span></li>)}</ul>}
        </details>
      </div>}
      {chat && <div className="space-y-2 border-t border-border pt-3">
        <h4 className="font-semibold">{chat.title}</h4>
        <div className="max-h-96 space-y-2 overflow-y-auto">{chat.messages.map((m: any, i: number) => <div key={i} className="rounded-lg bg-elevated/60 p-2"><div className="text-xs text-muted">{m.role}{m.attachments?.length ? ` · ${m.attachments.join(", ")}` : ""}</div><div className="whitespace-pre-wrap break-words">{m.content}</div></div>)}</div>
      </div>}
    </div>
  );
}

function Detail({ id, onClose }: { id: string; onClose: () => void }) {
  const L = useL();
  const { lang } = useI18n();
  const { data } = useSWR(`/api/v1/admin/people/${id}`, { refreshInterval: 10000 });
  return (
    <Modal open wide onOpenChange={(o) => !o && onClose()} title={data?.display_name || data?.username || ""} description={data?.username}>
      {!data ? <Skeleton className="h-40" /> : (
        <div className="space-y-4 text-sm">
          <p>{L("Espacio", "Space")}: <b>{bytes(data.used_bytes)}</b> · {L("Chats", "Chats")}: <b>{data.counts.conversations}</b> · {L("Archivos", "Files")}: <b>{data.counts.files}</b> · Workspaces: <b>{data.counts.workspaces}</b> · {L("Chats trabajando", "Chats working")}: <b>{data.turns_running}</b></p>
          <div>
            <h3 className="mb-2 font-semibold">{L("Trabajos en marcha", "Running jobs")}</h3>
            {!data.jobs_running.length ? <p className="text-muted">{L("Ninguno.", "None.")}</p> : (
              <Table head={[L("Tipo", "Type"), L("Estado", "Status"), L("Progreso", "Progress"), L("Inicio", "Started")]}>
                {data.jobs_running.map((j: any) => <tr key={j.id}><Td>{j.type}</Td><Td><Badge tone="accent">{j.status}</Badge></Td><Td>{Math.round((j.progress ?? 0) * 100)} %</Td><Td>{ago(j.started_at ?? j.created_at, lang)}</Td></tr>)}
              </Table>
            )}
          </div>
          {data.jobs_recent.length > 0 && <details>
            <summary className="cursor-pointer font-semibold">{L(`Últimos trabajos (${data.jobs_recent.length})`, `Latest jobs (${data.jobs_recent.length})`)}</summary>
            <div className="mt-2"><Table head={[L("Tipo", "Type"), L("Estado", "Status"), L("Fin", "Finished")]}>
              {data.jobs_recent.map((j: any) => <tr key={j.id}><Td>{j.type}</Td><Td>{j.status}</Td><Td>{ago(j.finished_at ?? j.created_at, lang)}</Td></tr>)}
            </Table></div>
          </details>}
          <div>
            <h3 className="mb-2 font-semibold">{L("Últimas peticiones", "Latest requests")}</h3>
            {!data.requests.length ? <p className="text-muted">{L("Ninguna.", "None.")}</p> : (
              <Table head={[L("Cuándo", "When"), L("Origen", "Source"), L("Modelo", "Model"), L("Estado", "Status"), "Tokens", L("Tiempo", "Time")]}>
                {data.requests.map((r: any, i: number) => <tr key={i}><Td>{dt(r.ts, lang)}</Td><Td>{r.source}</Td><Td>{r.model ?? "—"}</Td><Td>{r.status_code}</Td><Td>{compact(r.prompt_tokens + r.completion_tokens)}</Td><Td>{(r.latency_ms / 1000).toFixed(1)} s</Td></tr>)}
              </Table>
            )}
          </div>
          <ContentAccess id={id} name={data.display_name || data.username} consent={data.content_consent} />
        </div>
      )}
    </Modal>
  );
}

export function PeoplePanel() {
  const L = useL();
  const { lang } = useI18n();
  const { data } = useSWR("/api/v1/admin/people", { refreshInterval: 15000 });
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState<string | null>(null);
  const labels: Record<string, string> = {
    online: L("Conectado", "Online"), active: L("Activo", "Active"), inactive: L("Inactivo", "Inactive"),
    never: L("Sin actividad", "No activity"), disabled: L("Desactivada", "Disabled"),
  };
  const users: any[] = data?.users ?? [];
  const groups: Record<string, (u: any) => boolean> = {
    all: () => true, online: (u) => u.state === "online", active: (u) => u.state === "active",
    inactive: (u) => ["inactive", "never", "disabled"].includes(u.state), busy: (u) => u.jobs_running + u.turns_running > 0,
  };
  const names: Record<string, string> = { all: L("Todos", "All"), online: L("Conectados", "Online"), active: L("Activos", "Active"), inactive: L("No activos", "Not active"), busy: L("Con trabajo en marcha", "With work running") };
  return (
    <Card className="mt-4">
      <CardHeader icon={<Users className="h-4 w-4" />} title={L("Usuarios", "Users")}
        subtitle={data && L(`Conectado: actividad en los últimos ${data.online_minutes} minutos. Activo: en los últimos ${data.active_days} días.`, `Online: activity in the last ${data.online_minutes} minutes. Active: in the last ${data.active_days} days.`)} />
      {!data ? <Skeleton className="h-32" /> : <>
        <div className="flex flex-wrap gap-2 px-5 py-3">
          {Object.keys(groups).map((k) => <Button key={k} size="sm" variant={filter === k ? "primary" : "ghost"} onClick={() => setFilter(k)}>{names[k]} ({users.filter(groups[k]).length})</Button>)}
        </div>
        {!users.filter(groups[filter]).length ? <Empty title={L("Nadie en este grupo.", "Nobody in this group.")} /> : (
          <Table head={[L("Usuario", "User"), L("Estado", "Status"), L("Última actividad", "Last activity"), L("En marcha", "Running"), L("Peticiones (7 días)", "Requests (7 days)"), L("Tokens (7 días)", "Tokens (7 days)"), L("Espacio", "Space"), ""]}>
            {users.filter(groups[filter]).map((u) => <tr key={u.id}>
              <Td><b>{u.display_name || u.username}</b><div className="text-xs text-muted">{u.username} · {u.role}</div></Td>
              <Td><Badge tone={TONES[u.state]}>{labels[u.state]}</Badge></Td>
              <Td>{u.last_seen ? ago(u.last_seen, lang) : "—"}</Td>
              <Td>{u.jobs_running + u.turns_running > 0 ? <Badge tone="accent">{L(`${u.jobs_running} trabajos · ${u.turns_running} chats`, `${u.jobs_running} jobs · ${u.turns_running} chats`)}</Badge> : "—"}</Td>
              <Td>{u.requests_7d}</Td><Td>{compact(u.tokens_7d)}</Td><Td>{bytes(u.used_bytes)}</Td>
              <Td><Button size="sm" variant="ghost" onClick={() => setOpen(u.id)}>{L("Ver", "View")}</Button></Td>
            </tr>)}
          </Table>
        )}
      </>}
      {open && <Detail id={open} onClose={() => setOpen(null)} />}
    </Card>
  );
}
