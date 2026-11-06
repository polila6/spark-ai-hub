"use client";
import { CheckSquare, FolderKanban, MessageSquarePlus, Share2, Trash2, Upload, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";
import useSWR from "swr";
import { BulkBar, BulkDeleteConfirm, SelectBox, useBulk, useSelection } from "@/components/bulk";
import { hubFile, useFileViewer } from "@/components/file-viewer";
import { TextStatus, UploadDialog } from "@/components/files";
import { Guide } from "@/components/guide";
import { Badge, Button, Card, CardBody, CardHeader, Empty, ErrorBox, Field, Input, Modal, PageHeader, Select, Table, Td, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { ago, bytes } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useToast } from "@/lib/providers";

export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const L = useL();
  const viewer = useFileViewer();
  const { lang } = useI18n();
  const router = useRouter();
  const { push } = useToast();
  const { data: p, mutate, error } = useSWR(`/api/v1/projects/${id}`);
  const { data: files, mutate: mutF } = useSWR(`/api/v1/files?project_id=${id}`, { refreshInterval: (d: any) => (d?.some((f: any) => f.text_status === "pending" || f.knowledge?.some((k: any) => k.status !== "ready" && k.status !== "failed")) ? 4000 : 0) });
  const { data: models } = useSWR("/api/v1/models");
  const [f, setF] = useState<any>(null);
  const [up, setUp] = useState(false);
  const [share, setShare] = useState(false);
  const [sh, setSh] = useState({ username: "", permission: "read" });
  const [err, setErr] = useState<any>(null);
  // select several of the project's chats and delete them at once
  const [selecting, setSelecting] = useState(false);
  const [delChats, setDelChats] = useState(false);
  const chatList: any[] = p?.conversations_list ?? [];
  const chatSel = useSelection(chatList.map((c: any) => c.id as string));
  const bulkChats = useBulk("/api/v1/playground/conversations/bulk", () => { mutate(); });
  useEffect(() => { if (p) setF({ name: p.name, description: p.description, instructions: p.instructions, default_model: p.default_model ?? "" }); }, [p]);
  if (error) return <ErrorBox error={error} />;
  if (!p || !f) return null;
  const canWrite = p.permission === "owner" || p.permission === "write";
  const chat = (models ?? []).filter((m: any) => m.capabilities?.includes("chat") && ["installed", "ready"].includes(m.status));
  return (
    <div>
      <PageHeader title={p.name} subtitle={p.is_owner ? L("Tu proyecto", "Your project") : `${L("Compartido por", "Shared by")} ${p.owner} · ${p.permission === "write" ? L("escritura", "write") : L("lectura", "read")}`} icon={<FolderKanban className="h-5 w-5" />}
        actions={<>
          <Button variant="primary" onClick={() => router.push(`/playground?project=${p.id}`)}><MessageSquarePlus className="h-4 w-4" />{L("Nuevo chat en el proyecto", "New chat in project")}</Button>
          {p.is_owner && <Button onClick={() => setShare(true)}><Share2 className="h-4 w-4" />{L("Compartir", "Share")}</Button>}
        </>} />
      <Guide id="projects" />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="space-y-4">
          <Card>
            <CardHeader title={L("Documentos del proyecto", "Project documents")} subtitle={L("Se indexan automáticamente; el chat del proyecto busca en ellos", "Indexed automatically; project chats search them")}
              actions={canWrite && <Button size="sm" variant="primary" onClick={() => setUp(true)}><Upload className="h-3.5 w-3.5" />{L("Subir", "Upload")}</Button>} />
            {files && !files.length ? <Empty title={L("Sin documentos", "No documents")} hint={L("Sube PDF, Word, Markdown, código o datos.", "Upload PDF, Word, Markdown, code or data.")} /> : (
              <Table head={[L("Archivo", "File"), L("Tamaño", "Size"), L("Texto", "Text"), L("Índice", "Index"), ""]}>
                {files?.map((x: any) => <tr key={x.id}><Td className="max-w-[280px] truncate font-medium"><button type="button" className="max-w-full truncate text-left hover:text-accent" title={L("Ver", "View")} onClick={() => viewer.open(hubFile(x), files.map(hubFile))}>{x.filename}</button></Td><Td className="text-xs">{bytes(x.size_bytes)}</Td><Td><TextStatus f={x} /></Td>
                  <Td>{x.knowledge?.map((k: any, i: number) => <Badge key={i} tone={k.status === "ready" ? "ok" : k.status === "failed" ? "warn" : "info"}>{k.status === "ready" ? `${k.chunks}` : k.status}</Badge>)}</Td>
                  <Td>{p.is_owner && <Button size="icon" variant="ghost" aria-label={L("Quitar del proyecto", "Remove from project")} onClick={async () => { await api(`/api/v1/files/${x.id}`, { method: "DELETE" }); mutF(); }}><Trash2 className="h-4 w-4" /></Button>}</Td></tr>)}
              </Table>
            )}
          </Card>
          <Card>
            <CardHeader title={L("Tus chats en este proyecto", "Your chats in this project")}
              actions={chatList.length > 0 ? <Button size="sm" variant={selecting ? "outline" : "ghost"} aria-pressed={selecting} onClick={() => { setSelecting(!selecting); chatSel.clear(); }}><CheckSquare className="h-3.5 w-3.5" />{selecting ? L("Listo", "Done") : L("Seleccionar", "Select")}</Button> : undefined} />
            {!chatList.length ? <Empty title={L("Sin chats todavía", "No chats yet")} /> : (
              <>
                {selecting && (
                  <div className="space-y-2 border-b border-border px-5 py-2">
                    <label className="flex items-center gap-2 text-xs text-muted">
                      <SelectBox checked={chatSel.all} indeterminate={chatSel.some} onChange={() => chatSel.toggleAll()} label={L("Seleccionar todos los chats", "Select all chats")} />
                      {chatSel.count ? L(`${chatSel.count} seleccionados`, `${chatSel.count} selected`) : L("Marca los chats (Mayús para un rango)", "Tick the chats (Shift for a range)")}
                    </label>
                    <BulkBar count={chatSel.count} onClear={chatSel.clear} noun={[L("chat", "chat"), L("chats", "chats")]}
                      actions={[{ key: "delete", label: L("Borrar", "Delete"), icon: <Trash2 className="h-3.5 w-3.5" />, danger: true, onClick: () => setDelChats(true) }]} />
                  </div>
                )}
                <ul className="divide-y divide-border/60">{chatList.map((c: any) => <li key={c.id} className={chatSel.has(c.id) ? "bg-accent/10" : undefined}>
                  {selecting ? (
                    <div role="button" tabIndex={0} className="flex cursor-pointer items-center gap-3 px-5 py-2.5 text-sm hover:bg-elevated" onClick={(e) => chatSel.toggle(c.id, e.shiftKey)}
                      onKeyDown={(e) => { if (e.key === " " || e.key === "Enter") { e.preventDefault(); chatSel.toggle(c.id); } }}>
                      <SelectBox checked={chatSel.has(c.id)} onChange={(_, shift) => chatSel.toggle(c.id, shift)} label={L(`Seleccionar «${c.title}»`, `Select “${c.title}”`)} />
                      <span className="min-w-0 flex-1 truncate">{c.title}</span><span className="text-xs text-subtle">{ago(c.updated_at, lang)}</span>
                    </div>
                  ) : (
                    <Link href={`/playground?c=${c.id}`} className="flex justify-between px-5 py-2.5 text-sm hover:bg-elevated"><span className="truncate">{c.title}</span><span className="text-xs text-subtle">{ago(c.updated_at, lang)}</span></Link>
                  )}
                </li>)}</ul>
                <BulkDeleteConfirm open={delChats} onOpenChange={setDelChats} busy={bulkChats.busy} noun={[L("chat", "chat"), L("chats", "chats")]}
                  names={chatList.filter((c: any) => chatSel.has(c.id)).map((c: any) => c.title)}
                  onConfirm={async () => {
                    const r = await bulkChats.run({ ids: chatSel.list(), action: "delete" }, (n) => L(`${n} chats borrados`, `${n} chats deleted`));
                    chatSel.clear(); setDelChats(false);
                    if (r && r.skipped.length === 0) setSelecting(false);
                  }} />
              </>
            )}
          </Card>
        </div>
        <Card className="h-fit">
          <CardHeader title={L("Configuración", "Settings")} />
          <CardBody className="space-y-3">
            <Field label={L("Nombre", "Name")}><Input disabled={!canWrite} value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
            <Field label={L("Descripción", "Description")}><Input disabled={!canWrite} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
            <Field label={L("Instrucciones", "Instructions")}><Textarea disabled={!canWrite} rows={8} value={f.instructions} onChange={(e) => setF({ ...f, instructions: e.target.value })} /></Field>
            <Field label={L("Modelo por defecto", "Default model")}><Select disabled={!canWrite} value={f.default_model} onChange={(e) => setF({ ...f, default_model: e.target.value })}><option value="">—</option>{chat.map((m: any) => <option key={m.id} value={m.alias}>{m.alias}</option>)}</Select></Field>
            {canWrite && <div className="flex justify-between">
              {p.is_owner ? <Button variant="ghost" onClick={async () => { if (!window.confirm(L("¿Eliminar el proyecto? Sus chats se conservan fuera del proyecto.", "Delete the project? Its chats are kept outside the project."))) return; await api(`/api/v1/projects/${p.id}?delete_files=true`, { method: "DELETE" }); router.push("/projects"); }}><Trash2 className="h-4 w-4" />{L("Eliminar", "Delete")}</Button> : <span />}
              <Button variant="primary" onClick={async () => { await api(`/api/v1/projects/${p.id}`, { method: "PATCH", json: { ...f, default_model: f.default_model || null } }); push("ok", L("Guardado", "Saved")); mutate(); }}>{L("Guardar", "Save")}</Button></div>}
            {!p.is_owner && <Button variant="ghost" onClick={async () => { const me = await api("/api/v1/auth/me"); await api(`/api/v1/projects/${p.id}/shares/${me.user.id}`, { method: "DELETE" }); router.push("/projects"); }}>{L("Abandonar proyecto", "Leave project")}</Button>}
          </CardBody>
        </Card>
      </div>
      <UploadDialog open={up} onOpenChange={setUp} projectId={p.id} onDone={() => { mutF(); mutate(); }} />
      <Modal open={share} onOpenChange={setShare} title={L("Compartir proyecto", "Share project")} description={L("Tus compañeros verán documentos e instrucciones, no tus chats.", "Colleagues see documents and instructions, not your chats.")}>
        <div className="space-y-3">
          <div className="flex gap-2">
            <Input placeholder={L("usuario", "username")} value={sh.username} onChange={(e) => setSh({ ...sh, username: e.target.value.toLowerCase() })} />
            <Select className="w-36" value={sh.permission} onChange={(e) => setSh({ ...sh, permission: e.target.value })}><option value="read">{L("lectura", "read")}</option><option value="write">{L("escritura", "write")}</option></Select>
            <Button variant="primary" disabled={!sh.username} onClick={async () => { setErr(null); try { await api(`/api/v1/projects/${p.id}/shares`, { method: "POST", json: sh }); setSh({ username: "", permission: "read" }); mutate(); } catch (e) { setErr(e); } }}>{L("Añadir", "Add")}</Button>
          </div>
          {err && <ErrorBox error={err} />}
          <ul className="divide-y divide-border/60">{p.shares?.map((s: any) => <li key={s.user_id} className="flex items-center justify-between py-2 text-sm"><span>{s.username} <Badge>{s.permission}</Badge></span>
            <Button size="icon" variant="ghost" aria-label={L("Quitar", "Remove")} onClick={async () => { await api(`/api/v1/projects/${p.id}/shares/${s.user_id}`, { method: "DELETE" }); mutate(); }}><X className="h-4 w-4" /></Button></li>)}</ul>
        </div>
      </Modal>
    </div>
  );
}
