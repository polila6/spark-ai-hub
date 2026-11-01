"use client";
import { FolderKanban, Plus, Users } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import useSWR from "swr";
import { Guide, StepsEmpty } from "@/components/guide";
import { Badge, Button, Card, ErrorBox, Field, Input, Modal, PageHeader, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";

export default function ProjectsPage() {
  const L = useL();
  const { lang } = useI18n();
  const router = useRouter();
  const { data } = useSWR("/api/v1/projects");
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ name: "", description: "", instructions: "" });
  const [err, setErr] = useState<any>(null);
  return (
    <div>
      <PageHeader title={L("Proyectos", "Projects")} subtitle={L("Espacios con chats, documentos e instrucciones propias", "Workspaces with their own chats, documents and instructions")} icon={<FolderKanban className="h-5 w-5" />}
        actions={<Button variant="primary" onClick={() => setOpen(true)}><Plus className="h-4 w-4" />{L("Nuevo proyecto", "New project")}</Button>} />
      <Guide id="projects" />
      {data && !data.length ? (
        <Card><StepsEmpty icon={<FolderKanban className="h-5 w-5" />} title={L("Aún no tienes proyectos", "No projects yet")}
          steps={[L("Crea un proyecto con instrucciones (p. ej. «Revisor de mi tesis»).", "Create a project with instructions (e.g. “Reviewer of my thesis”)."), L("Sube sus documentos.", "Upload its documents."), L("Chatea dentro del proyecto o compártelo con tu grupo.", "Chat inside the project or share it with your group.")]}
          action={<Button variant="primary" onClick={() => setOpen(true)}><Plus className="h-4 w-4" />{L("Crear proyecto", "Create project")}</Button>} /></Card>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {data?.map((p: any) => (
            <Link key={p.id} href={`/projects/${p.id}`}>
              <Card className="h-full p-5 transition hover:border-accent/40">
                <div className="flex items-start justify-between gap-2"><h3 className="font-semibold">{p.name}</h3>{!p.is_owner ? <Badge tone="info">{L("de", "by")} {p.owner} · {p.permission === "write" ? L("escritura", "write") : L("lectura", "read")}</Badge> : p.shares?.length ? <Badge><Users className="h-3 w-3" />{p.shares.length}</Badge> : null}</div>
                <p className="mt-1 line-clamp-2 text-sm text-muted">{p.description || p.instructions || "—"}</p>
                <p className="mt-3 text-xs text-subtle">{p.files} {L("archivos", "files")} · {p.conversations} {L("chats", "chats")} · {ago(p.updated_at, lang)}</p>
              </Card>
            </Link>
          ))}
        </div>
      )}
      <Modal open={open} onOpenChange={setOpen} title={L("Nuevo proyecto", "New project")}>
        <div className="space-y-3">
          <Field label={L("Nombre", "Name")}><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label={L("Descripción", "Description")}><Input value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
          <Field label={L("Instrucciones para el asistente", "Instructions for the assistant")} hint={L("Se aplican a todos los chats del proyecto", "Applied to every chat in the project")}>
            <Textarea rows={5} value={f.instructions} onChange={(e) => setF({ ...f, instructions: e.target.value })} placeholder={L("Eres el asistente del proyecto X. Usa siempre los documentos del proyecto y cita las páginas…", "You are the assistant of project X. Always use the project documents and cite pages…")} /></Field>
          {err && <ErrorBox error={err} />}
          <div className="flex justify-end"><Button variant="primary" disabled={!f.name} onClick={async () => { try { const p = await api("/api/v1/projects", { method: "POST", json: f }); router.push(`/projects/${p.id}`); } catch (e) { setErr(e); } }}>{L("Crear", "Create")}</Button></div>
        </div>
      </Modal>
    </div>
  );
}
