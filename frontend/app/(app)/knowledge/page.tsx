"use client";
import { LibraryBig, Plus, Search, Trash2, Upload } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { Connectors } from "@/components/connectors";
import { UploadDialog } from "@/components/files";
import { Guide, StepsEmpty } from "@/components/guide";
import { Badge, Button, Card, CardBody, CardHeader, Empty, Field, Input, Modal, Notice, PageHeader, Table, Td, Textarea, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { bytes, dt } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";

export default function KnowledgePage() {
  const L = useL();
  const { lang } = useI18n();
  const { data, mutate } = useSWR("/api/v1/knowledge/collections");
  const [sel, setSel] = useState<string | null>(null);
  const cur = data?.collections?.find((c: any) => c.id === sel) ?? data?.collections?.[0];
  const { data: docs, mutate: mutD } = useSWR(cur ? `/api/v1/knowledge/collections/${cur.id}/documents` : null,
    { refreshInterval: (d: any) => (d?.some((x: any) => ["pending", "indexing"].includes(x.status)) ? 3000 : 0) });
  const [newOpen, setNewOpen] = useState(false);
  const [nf, setNf] = useState({ name: "", description: "" });
  const [up, setUp] = useState(false);
  const [q, setQ] = useState("");
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div>
      <PageHeader title={L("Base de conocimiento", "Knowledge base")} subtitle={L("Documentos indexados para que el asistente busque y cite", "Indexed documents the assistant searches and cites")} icon={<LibraryBig className="h-5 w-5" />}
        actions={<Button variant="primary" onClick={() => setNewOpen(true)}><Plus className="h-4 w-4" />{L("Nueva colección", "New collection")}</Button>} />
      <Guide id="knowledge" />
      {data && !data.embedding_model && <Notice tone="warn">{L("No hay modelo de embeddings instalado: la búsqueda funciona solo por palabras clave. Un administrador puede instalar «Qwen3-Embedding-0.6B».", "No embedding model installed: search works by keywords only. An administrator can install “Qwen3-Embedding-0.6B”.")}</Notice>}
      <div className="mt-4 grid gap-4 lg:grid-cols-[280px_minmax(0,1fr)]">
        <Card className="h-fit p-2">
          {data?.collections?.map((c: any) => (
            <button key={c.id} onClick={() => setSel(c.id)} className={cx("w-full rounded-lg px-3 py-2 text-left", cur?.id === c.id ? "bg-accent/12" : "hover:bg-elevated")}>
              <p className="truncate text-sm font-medium">{c.name}</p>
              <p className="text-[11px] text-muted">{c.documents} {L("documentos", "documents")} · {c.chunks} {L("fragmentos", "chunks")}{!c.is_owner && ` · ${L("compartida", "shared")}`}</p>
            </button>
          ))}
        </Card>
        <div className="space-y-4">
          {cur && (
            <Card>
              <CardHeader title={cur.name} subtitle={cur.description} actions={<>
                {cur.is_owner && !cur.project_id && <Button size="sm" variant="primary" onClick={() => setUp(true)}><Upload className="h-3.5 w-3.5" />{L("Subir documentos", "Upload documents")}</Button>}
                {cur.project_id && <a href={`/projects/${cur.project_id}`}><Button size="sm">{L("Abrir proyecto", "Open project")}</Button></a>}
                {cur.is_owner && !cur.project_id && cur.name !== "Mis documentos" && <Button size="sm" variant="ghost" onClick={async () => { if (!window.confirm(L("¿Eliminar la colección? Los archivos siguen en Mis archivos.", "Delete the collection? Files stay in My files."))) return; await api(`/api/v1/knowledge/collections/${cur.id}`, { method: "DELETE" }); setSel(null); mutate(); }}><Trash2 className="h-3.5 w-3.5" /></Button>}
              </>} />
              {docs && !docs.length ? (
                <StepsEmpty icon={<Upload className="h-5 w-5" />} title={L("Colección vacía", "Empty collection")} steps={[L("Pulsa «Subir documentos» (PDF, Word, Markdown, código…).", "Click “Upload documents” (PDF, Word, Markdown, code…)."), L("Espera a que el estado sea «ready».", "Wait until the status is “ready”."), L("Pregunta en el chat: el asistente buscará aquí.", "Ask in the chat: the assistant will search here.")]} />
              ) : (
                <Table head={[L("Documento", "Document"), L("Tamaño", "Size"), L("Estado", "Status"), L("Fragmentos", "Chunks"), L("Búsqueda", "Search"), L("Añadido", "Added"), ""]}>
                  {docs?.map((d: any) => (
                    <tr key={d.id}><Td className="max-w-[260px] truncate font-medium">{d.filename}</Td><Td className="text-xs">{bytes(d.size_bytes)}</Td>
                      <Td><Badge tone={d.status === "ready" ? "ok" : d.status === "failed" ? "danger" : "info"} title={d.error}>{d.status}</Badge>{d.error && <p className="mt-1 max-w-[220px] text-[10px] text-danger">{d.error}</p>}</Td>
                      <Td>{d.chunks}</Td><Td className="text-xs text-muted">{d.embedding_model === "fts-only" ? L("palabras clave", "keywords") : d.embedding_model ? L("semántica + palabras", "semantic + keywords") : "—"}</Td>
                      <Td className="whitespace-nowrap text-xs text-muted">{dt(d.created_at, lang)}</Td>
                      <Td>{cur.is_owner && <Button size="icon" variant="ghost" aria-label={L("Quitar", "Remove")} onClick={async () => { await api(`/api/v1/knowledge/documents/${d.id}`, { method: "DELETE" }); mutD(); mutate(); }}><Trash2 className="h-4 w-4" /></Button>}</Td></tr>
                  ))}
                </Table>
              )}
            </Card>
          )}
          {data && <Connectors collections={data.collections ?? []} />}
          <Card>
            <CardHeader title={L("Probar búsqueda", "Test search")} subtitle={L("Lo que encontraría el asistente con search_knowledge (en todas tus colecciones)", "What the assistant would find with search_knowledge (all your collections)")} icon={<Search className="h-4 w-4" />} />
            <CardBody className="space-y-3">
              <form className="flex gap-2" onSubmit={async (e) => { e.preventDefault(); setBusy(true); try { setRes(await api("/api/v1/knowledge/search", { method: "POST", json: { query: q, k: 8 } })); } finally { setBusy(false); } }}>
                <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={L("p. ej. ¿qué conclusiones tiene el informe de 2025?", "e.g. what are the conclusions of the 2025 report?")} />
                <Button type="submit" variant="primary" loading={busy} disabled={!q.trim()}><Search className="h-4 w-4" /></Button>
              </form>
              {res && (res.results.length ? <div className="space-y-2">
                <p className="text-[11px] text-muted">{L("Modo", "Mode")}: {res.mode}</p>
                {res.results.map((r: any) => <div key={r.chunk_id} className="rounded-lg border border-border p-3">
                  <p className="mb-1 text-xs font-medium">{r.filename}{r.page ? `, p. ${r.page}` : ""} <span className="text-muted">· {r.collection}</span></p>
                  <p className="line-clamp-4 whitespace-pre-wrap text-xs text-muted">{r.text}</p></div>)}
              </div> : <Empty title={L("Sin resultados", "No results")} />)}
            </CardBody>
          </Card>
        </div>
      </div>
      <Modal open={newOpen} onOpenChange={setNewOpen} title={L("Nueva colección", "New collection")}>
        <div className="space-y-3">
          <Field label={L("Nombre", "Name")}><Input value={nf.name} onChange={(e) => setNf({ ...nf, name: e.target.value })} /></Field>
          <Field label={L("Descripción", "Description")}><Textarea rows={3} value={nf.description} onChange={(e) => setNf({ ...nf, description: e.target.value })} /></Field>
          <div className="flex justify-end"><Button variant="primary" disabled={!nf.name} onClick={async () => { const c = await api("/api/v1/knowledge/collections", { method: "POST", json: nf }); setNewOpen(false); setNf({ name: "", description: "" }); await mutate(); setSel(c.id); }}>{L("Crear", "Create")}</Button></div>
        </div>
      </Modal>
      <UploadDialog open={up} onOpenChange={setUp} onDone={() => { mutD(); mutate(); }} />
    </div>
  );
}
