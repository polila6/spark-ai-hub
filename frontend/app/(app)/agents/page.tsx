"use client";
import { Bot, Globe, Pencil, Plus, Trash2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { Guide } from "@/components/guide";
import { Badge, Button, Card, Field, Input, Modal, Notice, PageHeader, Select, Skeleton, Switch, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { useL } from "@/lib/i18n";
import { isManager, useMe, useToast } from "@/lib/providers";

type Ag = { id?: string; name: string; description: string; instructions: string; tools: string[]; skills: string[]; visibility: string; enabled: boolean };
const EMPTY: Ag = { name: "", description: "", instructions: "", tools: ["web_search", "fetch_url"], skills: [], visibility: "private", enabled: true };

function Editor({ value, tools, onClose, onSaved }: { value: Ag; tools: { name: string; label: string }[]; onClose: () => void; onSaved: () => void }) {
  const L = useL();
  const { me } = useMe();
  const { push } = useToast();
  const [a, setA] = useState<Ag>(value);
  const { data: sk } = useSWR("/api/v1/skills");
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try {
      const json = { ...a, id: undefined };
      await api(a.id ? `/api/v1/agents/${a.id}` : "/api/v1/agents", { method: a.id ? "PUT" : "POST", json });
      push("ok", L("Agente guardado", "Agent saved"));
      onSaved();
    } catch (e: any) { push("error", e.message); } finally { setBusy(false); }
  }
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} wide title={a.id ? L("Editar agente", "Edit agent") : L("Nuevo agente", "New agent")}
      description={L("El asistente del chat podrá delegarle tareas. Trabaja con su propio contexto y le devuelve un informe.", "The chat assistant can delegate tasks to it. It works with its own context and returns a report.")}>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label={L("Nombre", "Name")} hint={L("minúsculas y guiones, p. ej. revisor-normativa", "lower case and dashes")}><Input value={a.name} onChange={(e) => setA({ ...a, name: e.target.value })} /></Field>
        <Field label={L("Para qué sirve", "What it is for")} hint={L("El asistente lo lee para decidir cuándo usarlo", "The assistant reads it to decide when to use it")}><Input value={a.description} onChange={(e) => setA({ ...a, description: e.target.value })} /></Field>
        <div className="md:col-span-2"><Field label={L("Instrucciones", "Instructions")} hint={L("Rol, método, formato del informe que debe devolver", "Role, method, report format")}>
          <Textarea rows={7} value={a.instructions} onChange={(e) => setA({ ...a, instructions: e.target.value })} placeholder={L("Eres un experto en normativa de seguridad de máquinas (Directiva 2006/42/CE). Busca la norma aplicable, léela y devuelve los requisitos con su artículo…", "You are an expert in machinery safety regulations…")} /></Field></div>
        <div>
          <p className="mb-1.5 text-xs font-medium text-muted">{L("Herramientas que puede usar", "Tools it may use")}</p>
          <div className="space-y-1.5">{tools.map((t) => <Switch key={t.name} checked={a.tools.includes(t.name)} label={t.label} onChange={(v) => setA({ ...a, tools: v ? [...a.tools, t.name] : a.tools.filter((x) => x !== t.name) })} />)}</div>
        </div>
        <div className="space-y-3">
          <Field label={L("Skill precargada (opcional)", "Preloaded skill (optional)")} hint={L("Se añade a sus instrucciones", "Added to its instructions")}>
            <Select value={a.skills[0] ?? ""} onChange={(e) => setA({ ...a, skills: e.target.value ? [e.target.value] : [] })}>
              <option value="">—</option>{(sk?.skills ?? []).map((s: any) => <option key={s.id} value={s.name}>{s.name}</option>)}
            </Select></Field>
          {isManager(me) && <Switch checked={a.visibility === "public"} onChange={(v) => setA({ ...a, visibility: v ? "public" : "private" })} label={L("Disponible para todos los usuarios", "Available to all users")} />}
          <Switch checked={a.enabled} onChange={(v) => setA({ ...a, enabled: v })} label={L("Activado", "Enabled")} />
        </div>
      </div>
      <div className="mt-4 flex justify-end gap-2"><Button variant="ghost" onClick={onClose}>{L("Cancelar", "Cancel")}</Button><Button variant="primary" loading={busy} disabled={!a.name || !a.instructions} onClick={save}>{L("Guardar", "Save")}</Button></div>
    </Modal>
  );
}

export default function AgentsPage() {
  const L = useL();
  const { data, mutate } = useSWR("/api/v1/agents");
  const [edit, setEdit] = useState<Ag | null>(null);
  const label = (n: string) => data?.tools.find((t: any) => t.name === n)?.label ?? n;
  return (
    <div>
      <PageHeader title={L("Agentes", "Agents")} subtitle={L("Ayudantes especializados a los que el chat delega tareas, incluso varios a la vez", "Specialised helpers the chat delegates tasks to, even several at once")} icon={<Bot className="h-5 w-5" />}
        actions={<Button variant="primary" onClick={() => setEdit({ ...EMPTY })}><Plus className="h-4 w-4" />{L("Nuevo agente", "New agent")}</Button>} />
      <Guide id="agents" />
      {!data ? <Skeleton className="h-40" /> : (
        <>
          {!data.settings.enabled && <Notice tone="warn">{L("El administrador ha desactivado los agentes en el chat.", "The administrator has disabled agents in the chat.")}</Notice>}
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {data.agents.map((a: any) => (
              <Card key={a.id} className="flex flex-col p-4">
                <div className="flex items-center gap-2"><Bot className="h-4 w-4 text-accent" /><span className="font-medium">{a.name}</span>
                  {a.builtin ? <Badge>{L("incluido", "built-in")}</Badge> : a.visibility === "public" ? <Badge tone="accent"><Globe className="h-3 w-3" />{L("para todos", "for everyone")}</Badge> : <Badge>{L("tuyo", "yours")}</Badge>}
                  {!a.enabled && <Badge tone="warn">{L("desactivado", "disabled")}</Badge>}</div>
                <p className="mt-1.5 flex-1 text-xs text-muted">{a.description}</p>
                <div className="mt-2 flex flex-wrap gap-1">{a.tools.length ? a.tools.map((t: string) => <Badge key={t}>{label(t)}</Badge>) : <Badge>{L("sin herramientas (solo razona)", "no tools (reasoning only)")}</Badge>}{a.skills?.map((s: string) => <Badge key={s} tone="info">skill: {s}</Badge>)}</div>
                {a.mine && <div className="mt-3 flex gap-1"><Button size="sm" variant="ghost" onClick={() => setEdit(a)}><Pencil className="h-3.5 w-3.5" />{L("Editar", "Edit")}</Button>
                  <Button size="sm" variant="ghost" onClick={async () => { await api(`/api/v1/agents/${a.id}`, { method: "DELETE" }); mutate(); }}><Trash2 className="h-3.5 w-3.5" />{L("Borrar", "Delete")}</Button></div>}
              </Card>
            ))}
          </div>
          <p className="mt-4 text-xs text-muted">{L(`Hasta ${data.settings.max_parallel} agentes trabajan a la vez. Pruébalo en el `, `Up to ${data.settings.max_parallel} agents work at once. Try it in the `)}<Link href="/playground" className="text-accent">{L("chat", "chat")}</Link>{L(": «lanza un investigador para … y otro para …».", ": “run one researcher for … and another for …”.")}</p>
        </>
      )}
      {edit && data && <Editor value={edit} tools={data.tools} onClose={() => setEdit(null)} onSaved={() => { setEdit(null); mutate(); }} />}
    </div>
  );
}
