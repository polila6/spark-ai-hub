"use client";
import { AlarmClock, Mail, MessagesSquare, Pencil, Play, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { Guide } from "@/components/guide";
import { Badge, Button, Card, Field, Input, Modal, Notice, PageHeader, Select, Skeleton, Switch, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { dt } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useToast } from "@/lib/providers";

type Task = { id?: string; name: string; prompt: string; schedule: any; timezone: string; model_alias?: string | null; options: any; enabled: boolean };
const DAYS_ES = ["L", "M", "X", "J", "V", "S", "D"];
const DAYS_EN = ["M", "T", "W", "T", "F", "S", "S"];
const TZ = typeof Intl !== "undefined" ? Intl.DateTimeFormat().resolvedOptions().timeZone : "Europe/Madrid";
const EMPTY: Task = { name: "", prompt: "", schedule: { freq: "daily", time: "08:00", weekdays: [0, 1, 2, 3, 4] }, timezone: TZ, options: { web: true, email: false }, enabled: true };

const EXAMPLES = [
  { es: ["Novedades de arXiv", "Busca en arXiv los artículos publicados esta semana sobre robótica blanda y resume los 5 más relevantes con su enlace."], en: ["arXiv digest", "Search arXiv for papers published this week on soft robotics and summarise the 5 most relevant with links."], schedule: { freq: "weekly", weekday: 0, time: "09:00" } },
  { es: ["Noticias del sector", "Busca las noticias de hoy sobre impresión 3D industrial y hazme un resumen de 5 puntos con fuentes."], en: ["Industry news", "Find today's news about industrial 3D printing and give me a 5-point summary with sources."], schedule: { freq: "daily", time: "08:30", weekdays: [0, 1, 2, 3, 4] } },
];

function Editor({ value, onClose, onSaved, mailEnabled }: { value: Task; onClose: () => void; onSaved: () => void; mailEnabled: boolean }) {
  const L = useL();
  const { lang } = useI18n();
  const { push } = useToast();
  const [t, setT] = useState<Task>(value);
  const [busy, setBusy] = useState(false);
  const { data: models } = useSWR("/api/v1/models");
  const s = t.schedule;
  const setS = (patch: any) => setT({ ...t, schedule: { ...s, ...patch } });
  async function save() {
    setBusy(true);
    try {
      await api(t.id ? `/api/v1/tasks/${t.id}` : "/api/v1/tasks", { method: t.id ? "PUT" : "POST", json: { ...t, id: undefined } });
      push("ok", L("Tarea guardada", "Task saved")); onSaved();
    } catch (e: any) { push("error", e.message); } finally { setBusy(false); }
  }
  const days = lang === "en" ? DAYS_EN : DAYS_ES;
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} wide title={t.id ? L("Editar tarea", "Edit task") : L("Nueva tarea programada", "New scheduled task")}
      description={L("El asistente ejecutará estas instrucciones solo, en su propio chat, y te avisará con el resultado.", "The assistant runs these instructions on its own, in its own chat, and notifies you with the result.")}>
      <div className="space-y-3">
        {!t.id && <div className="flex flex-wrap gap-2 text-xs">{L("Ejemplos:", "Examples:")}{EXAMPLES.map((ex) => { const [n, p] = lang === "en" ? ex.en : ex.es; return <button key={n} type="button" className="rounded-full border border-border px-2 py-0.5 hover:bg-elevated" onClick={() => setT({ ...t, name: n, prompt: p, schedule: ex.schedule })}>{n}</button>; })}</div>}
        <Field label={L("Nombre", "Name")}><Input value={t.name} onChange={(e) => setT({ ...t, name: e.target.value })} /></Field>
        <Field label={L("Instrucciones", "Instructions")} hint={L("Escríbelas completas: cada ejecución empieza sin recordar la conversación anterior, salvo lo que esté en su chat.", "Write them in full: each run only sees its own chat.")}>
          <Textarea rows={5} value={t.prompt} onChange={(e) => setT({ ...t, prompt: e.target.value })} /></Field>
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label={L("Frecuencia", "Frequency")}><Select value={s.freq} onChange={(e) => setS({ freq: e.target.value })}>
            <option value="hourly">{L("Cada hora", "Hourly")}</option><option value="daily">{L("Diaria", "Daily")}</option><option value="weekly">{L("Semanal", "Weekly")}</option><option value="monthly">{L("Mensual", "Monthly")}</option></Select></Field>
          {s.freq === "hourly" ? <>
            <Field label={L("Cada (horas)", "Every (hours)")}><Input type="number" min={1} max={24} value={s.every ?? 1} onChange={(e) => setS({ every: Number(e.target.value) })} /></Field>
            <Field label={L("Minuto", "Minute")}><Input type="number" min={0} max={59} value={s.minute ?? 0} onChange={(e) => setS({ minute: Number(e.target.value) })} /></Field>
          </> : <Field label={L("Hora", "Time")}><Input type="time" value={s.time ?? "08:00"} onChange={(e) => setS({ time: e.target.value })} /></Field>}
          {s.freq === "weekly" && <Field label={L("Día", "Day")}><Select value={s.weekday ?? 0} onChange={(e) => setS({ weekday: Number(e.target.value) })}>{(lang === "en" ? ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"] : ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]).map((d, i) => <option key={i} value={i}>{d}</option>)}</Select></Field>}
          {s.freq === "monthly" && <Field label={L("Día del mes", "Day of month")}><Input type="number" min={1} max={28} value={s.day ?? 1} onChange={(e) => setS({ day: Number(e.target.value) })} /></Field>}
        </div>
        {s.freq === "daily" && <div className="flex flex-wrap items-center gap-1.5 text-xs"><span className="text-muted">{L("Días:", "Days:")}</span>{days.map((d, i) => { const on = (s.weekdays ?? [0, 1, 2, 3, 4, 5, 6]).includes(i); return <button key={i} type="button" className={`h-7 w-7 rounded-full border ${on ? "border-accent bg-accent text-accent-fg" : "border-border"}`} onClick={() => setS({ weekdays: on ? (s.weekdays ?? []).filter((x: number) => x !== i) : [...(s.weekdays ?? []), i].sort() })}>{d}</button>; })}</div>}
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={L("Modelo", "Model")} hint={L("Vacío = el modelo general", "Empty = the general model")}><Select value={t.model_alias ?? ""} onChange={(e) => setT({ ...t, model_alias: e.target.value || null })}><option value="">—</option>{(models ?? []).filter((m: any) => m.capabilities?.includes("chat") && m.tools_enabled).map((m: any) => <option key={m.id}>{m.alias}</option>)}</Select></Field>
          <Field label={L("Zona horaria", "Time zone")}><Input value={t.timezone} onChange={(e) => setT({ ...t, timezone: e.target.value })} /></Field>
        </div>
        <div className="flex flex-wrap gap-5">
          <Switch checked={!!t.options.web} onChange={(v) => setT({ ...t, options: { ...t.options, web: v } })} label={L("Puede buscar en internet", "May search the web")} />
          <Switch checked={!!t.options.research} onChange={(v) => setT({ ...t, options: { ...t.options, research: v, web: v || t.options.web } })} label={L("Investigación a fondo (informe)", "Deep research (report)")} />
          <Switch checked={!!t.options.email} onChange={(v) => setT({ ...t, options: { ...t.options, email: v } })} label={L("Enviarme el resultado por correo", "E-mail me the result")} />
          <Switch checked={t.enabled} onChange={(v) => setT({ ...t, enabled: v })} label={L("Activada", "Enabled")} />
        </div>
        {t.options.email && !mailEnabled && <Notice tone="warn">{L("El correo saliente no está configurado: recibirás el aviso en la campana, pero no el correo (Ajustes → Acceso y correo).", "Outgoing mail is not configured: you will get the bell notice but no e-mail.")}</Notice>}
      </div>
      <div className="mt-4 flex justify-end gap-2"><Button variant="ghost" onClick={onClose}>{L("Cancelar", "Cancel")}</Button><Button variant="primary" loading={busy} disabled={!t.name || !t.prompt} onClick={save}>{L("Guardar", "Save")}</Button></div>
    </Modal>
  );
}

export default function TasksPage() {
  const L = useL();
  const { lang } = useI18n();
  const { push } = useToast();
  const { data, mutate } = useSWR("/api/v1/tasks", { refreshInterval: 10000 });
  const [edit, setEdit] = useState<Task | null>(null);
  return (
    <div>
      <PageHeader title={L("Tareas programadas", "Scheduled tasks")} subtitle={L("El asistente trabaja por ti a la hora que digas: resúmenes, vigilancia de novedades, informes…", "The assistant works for you on schedule: digests, monitoring, reports…")} icon={<AlarmClock className="h-5 w-5" />}
        actions={<Button variant="primary" onClick={() => setEdit({ ...EMPTY })}><Plus className="h-4 w-4" />{L("Nueva tarea", "New task")}</Button>} />
      <Guide id="tasks" />
      {!data ? <Skeleton className="h-40" /> : data.tasks.length === 0 ? (
        <Card className="p-8 text-center text-sm text-muted">{L("Aún no tienes tareas. Crea una aquí o pídela en el chat: «cada lunes a las 9, resúmeme…».", "No tasks yet. Create one here or ask in the chat: “every Monday at 9, summarise…”.")}</Card>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          {data.tasks.map((t: any) => (
            <Card key={t.id} className="flex flex-col p-4">
              <div className="flex items-center gap-2"><AlarmClock className="h-4 w-4 text-accent" /><span className="font-medium">{t.name}</span>
                {!t.enabled && <Badge tone="warn">{L("pausada", "paused")}</Badge>}
                {t.last_status === "running" && <Badge tone="info">{L("ejecutándose", "running")}</Badge>}
                {t.last_status === "error" && <Badge tone="danger">{L("último intento falló", "last run failed")}</Badge>}
                {t.options?.email && <Mail className="h-3.5 w-3.5 text-muted" />}</div>
              <p className="mt-1 text-xs text-accent">{t.schedule_text}</p>
              <p className="mt-1.5 line-clamp-3 flex-1 text-xs text-muted">{t.prompt}</p>
              <p className="mt-2 text-[11px] text-subtle">{t.next_run_at && t.enabled ? `${L("Próxima", "Next")}: ${dt(t.next_run_at, lang)}` : ""}{t.last_run_at ? ` · ${L("Última", "Last")}: ${dt(t.last_run_at, lang)}` : ""}{t.run_count ? ` · ${t.run_count} ${L("ejecuciones", "runs")}` : ""}</p>
              {t.last_error && <p className="mt-1 text-[11px] text-danger">{t.last_error}</p>}
              <div className="mt-3 flex flex-wrap gap-1">
                <Button size="sm" variant="outline" disabled={t.last_status === "running"} onClick={async () => { try { await api(`/api/v1/tasks/${t.id}/run`, { method: "POST" }); push("ok", L("Ejecutando: te avisaremos al terminar", "Running: you will be notified")); setTimeout(mutate, 1500); } catch (e: any) { push("error", e.message); } }}><Play className="h-3.5 w-3.5" />{L("Ejecutar ahora", "Run now")}</Button>
                {t.conversation_id && <Link href={`/playground?c=${t.conversation_id}`}><Button size="sm" variant="ghost"><MessagesSquare className="h-3.5 w-3.5" />{L("Ver resultados", "Results")}</Button></Link>}
                <Button size="sm" variant="ghost" onClick={() => setEdit(t)}><Pencil className="h-3.5 w-3.5" />{L("Editar", "Edit")}</Button>
                <Button size="sm" variant="ghost" onClick={async () => { await api(`/api/v1/tasks/${t.id}`, { method: "DELETE" }); mutate(); }}><Trash2 className="h-3.5 w-3.5" /></Button>
              </div>
            </Card>
          ))}
        </div>
      )}
      {edit && <Editor value={edit} mailEnabled={!!data?.mail_enabled} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); mutate(); }} />}
    </div>
  );
}
