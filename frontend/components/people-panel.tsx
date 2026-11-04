"use client";
import { Users } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { Badge, Button, Card, CardHeader, Empty, Modal, Notice, Skeleton, Table, Td } from "@/components/ui";
import { ago, bytes, compact, dt } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";

const TONES: Record<string, "ok" | "accent" | "muted" | "warn"> = { online: "ok", active: "accent", inactive: "muted", never: "muted", disabled: "warn" };

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
          <Notice tone="muted">{L("Solo datos de uso. El contenido de chats y archivos es privado: las normas que aceptó cada usuario dicen que los administradores no lo leen.", "Usage data only. The content of chats and files is private: the terms each user accepted say administrators do not read it.")}</Notice>
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
