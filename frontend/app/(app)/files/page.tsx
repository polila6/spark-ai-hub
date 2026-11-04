"use client";
import { Download, FileText, Folder, FolderOpen, Image as ImageIcon, MessageSquare, RefreshCw, Table2, Trash2, Upload } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { hubFile, useFileViewer } from "@/components/file-viewer";
import { TextStatus, UploadDialog } from "@/components/files";
import { Guide, StepsEmpty } from "@/components/guide";
import { Badge, Button, Card, CardBody, ErrorBox, Input, Modal, PageHeader, Progress, Select, Table, Td, Tip, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { bytes, dt } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useToast } from "@/lib/providers";

const KIND_ICON: Record<string, any> = { document: FileText, image: ImageIcon, data: Table2 };

export default function FilesPage() {
  const L = useL();
  const { lang } = useI18n();
  const router = useRouter();
  const { push } = useToast();
  const [folder, setFolder] = useState<string | null>(null);
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const viewer = useFileViewer();
  const params = new URLSearchParams();
  if (folder !== null) params.set("folder", folder);
  if (kind) params.set("kind", kind);
  if (q) params.set("q", q);
  const { data, mutate, error } = useSWR(`/api/v1/files?${params}`, { refreshInterval: (d: any) => (d?.some((f: any) => f.text_status === "pending" || f.knowledge?.some((k: any) => ["pending", "indexing"].includes(k.status))) ? 4000 : 0) });
  const { data: folders, mutate: mutF } = useSWR("/api/v1/files/folders");
  const { data: acct } = useSWR("/api/v1/account/summary");
  const folderNames = useMemo(() => (folders ?? []).map((f: any) => f.folder).filter(Boolean).sort(), [folders]);
  const dragRef = useRef(0);
  return (
    <div onDragEnter={() => { dragRef.current++; setOpen(true); }}>
      <PageHeader title={L("Mis archivos", "My files")} subtitle={L("Tu disco privado en el Spark", "Your private drive on the Spark")} icon={<FolderOpen className="h-5 w-5" />}
        actions={<Button variant="primary" onClick={() => setOpen(true)}><Upload className="h-4 w-4" />{L("Subir", "Upload")}</Button>} />
      <Guide id="files" />
      {acct && <Card className="mb-4 p-4"><div className="mb-1.5 flex justify-between text-xs"><span className="text-muted">{L("Espacio usado", "Space used")}</span><span>{bytes(acct.used_bytes)} / {acct.quota_gb} GB</span></div>
        <Progress value={(acct.used_bytes / (acct.quota_gb * 1e9)) * 100} tone={acct.used_bytes / (acct.quota_gb * 1e9) > 0.85 ? "warn" : "accent"} /></Card>}
      <div className="grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
        <Card className="h-fit p-2">
          <button className={cx("flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-sm", folder === null ? "bg-accent/12 font-medium" : "hover:bg-elevated")} onClick={() => setFolder(null)}><FolderOpen className="h-4 w-4 text-accent" />{L("Todos", "All")}</button>
          {(folders ?? []).sort((a: any, b: any) => a.folder.localeCompare(b.folder)).map((f: any) => (
            <button key={f.folder || "root"} className={cx("flex w-full items-center gap-2 rounded-lg px-3 py-1.5 text-left text-sm", folder === f.folder ? "bg-accent/12 font-medium" : "hover:bg-elevated")} onClick={() => setFolder(f.folder)}>
              <Folder className="h-4 w-4 shrink-0 text-subtle" /><span className="truncate">{f.folder || L("(raíz)", "(root)")}</span><span className="ml-auto text-[10px] text-subtle">{f.files}</span>
            </button>))}
        </Card>
        <Card>
          <div className="flex flex-wrap gap-2 border-b border-border p-3">
            <Input className="max-w-xs" placeholder={L("Buscar por nombre…", "Search by name…")} value={q} onChange={(e) => setQ(e.target.value)} />
            <Select className="w-auto" value={kind} onChange={(e) => setKind(e.target.value)}>
              <option value="">{L("Todos los tipos", "All types")}</option><option value="document">{L("Documentos", "Documents")}</option><option value="data">{L("Datos", "Data")}</option><option value="image">{L("Imágenes", "Images")}</option><option value="audio">Audio</option><option value="other">{L("Otros", "Other")}</option></Select>
          </div>
          {error && <CardBody><ErrorBox error={error} /></CardBody>}
          {data && !data.length ? (
            <StepsEmpty icon={<Upload className="h-5 w-5" />} title={L("Aún no tienes archivos", "No files yet")}
              steps={[L("Pulsa «Subir» o arrastra archivos aquí.", "Click “Upload” or drag files here."), L("Déjalos en la base de conocimiento para que el chat pueda buscar en ellos.", "Keep them in the knowledge base so the chat can search them."), L("Abre el chat y pregunta por su contenido.", "Open the chat and ask about their content.")]}
              action={<Button variant="primary" onClick={() => setOpen(true)}><Upload className="h-4 w-4" />{L("Subir archivos", "Upload files")}</Button>} />
          ) : (
            <Table head={[L("Nombre", "Name"), L("Carpeta", "Folder"), L("Tamaño", "Size"), L("Texto", "Text"), L("Conocimiento", "Knowledge"), L("Fecha", "Date"), ""]}>
              {data?.map((f: any) => {
                const Icon = KIND_ICON[f.kind] ?? FileText;
                return (
                  <tr key={f.id}>
                    <Td><button className="flex items-center gap-2 text-left font-medium hover:text-accent" onClick={() => viewer.open(hubFile(f), (data ?? []).map(hubFile))}><Icon className="h-4 w-4 shrink-0 text-subtle" /><span className="max-w-[280px] truncate">{f.filename}</span></button></Td>
                    <Td className="text-xs text-muted">{f.folder || "—"}</Td>
                    <Td className="text-xs">{bytes(f.size_bytes)}</Td>
                    <Td><TextStatus f={f} /></Td>
                    <Td>{f.knowledge?.length ? f.knowledge.map((k: any, i: number) => <Badge key={i} tone={k.status === "ready" ? "ok" : k.status === "failed" ? "warn" : "info"} title={k.error}>{k.status === "ready" ? `${k.chunks} ${L("frag.", "chunks")}` : k.status}</Badge>) : <span className="text-subtle">—</span>}</Td>
                    <Td className="whitespace-nowrap text-xs text-muted">{dt(f.created_at, lang)}</Td>
                    <Td><div className="flex justify-end gap-0.5">
                      <Tip text={L("Preguntar en el chat", "Ask in chat")}><Button size="icon" variant="ghost" onClick={() => router.push(`/playground?attach=${f.id}`)}><MessageSquare className="h-4 w-4" /></Button></Tip>
                      <Tip text={L("Descargar", "Download")}><a href={`${f.url}?download=true`}><Button size="icon" variant="ghost"><Download className="h-4 w-4" /></Button></a></Tip>
                      <Tip text={L("Mover a carpeta", "Move to folder")}><Button size="icon" variant="ghost" onClick={async () => { const nf = window.prompt(L("Carpeta de destino", "Target folder"), f.folder); if (nf !== null) { await api(`/api/v1/files/${f.id}`, { method: "PATCH", json: { folder: nf } }); mutate(); mutF(); } }}><Folder className="h-4 w-4" /></Button></Tip>
                      {f.kind !== "image" && <Tip text={L("Volver a procesar", "Reprocess")}><Button size="icon" variant="ghost" onClick={async () => { await api(`/api/v1/files/${f.id}/reprocess`, { method: "POST" }); mutate(); }}><RefreshCw className="h-4 w-4" /></Button></Tip>}
                      <Tip text={L("Eliminar", "Delete")}><Button size="icon" variant="ghost" onClick={async () => { if (!window.confirm(L(`¿Eliminar ${f.filename}?`, `Delete ${f.filename}?`))) return; await api(`/api/v1/files/${f.id}`, { method: "DELETE" }); push("ok", L("Eliminado", "Deleted")); mutate(); mutF(); }}><Trash2 className="h-4 w-4" /></Button></Tip>
                    </div></Td>
                  </tr>
                );
              })}
            </Table>
          )}
        </Card>
      </div>
      <UploadDialog open={open} onOpenChange={setOpen} onDone={() => { mutate(); mutF(); }} folders={folderNames} />
    </div>
  );
}
