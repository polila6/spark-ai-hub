"use client";
import { CheckCircle2, Clock, Upload, XCircle } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { Field, Input, Modal, Progress, Select, Switch, Tip, Button } from "@/components/ui";
import { upload } from "@/lib/api";
import { bytes } from "@/lib/format";
import { useL } from "@/lib/i18n";

export function TextStatus({ f }: { f: any }) {
  const L = useL();
  if (f.text_status === "ready") return <Tip text={L(`${f.text_chars.toLocaleString()} caracteres extraídos`, `${f.text_chars.toLocaleString()} characters extracted`)}><span><CheckCircle2 className="h-4 w-4 text-ok" /></span></Tip>;
  if (f.text_status === "pending") return <Tip text={L("Procesando…", "Processing…")}><span><Clock className="h-4 w-4 animate-pulse text-info" /></span></Tip>;
  if (f.text_status === "failed") return <Tip text={f.meta?.extract_error || L("Sin texto", "No text")}><span><XCircle className="h-4 w-4 text-warn" /></span></Tip>;
  return <span className="text-subtle">—</span>;
}

export function UploadDialog({ open, onOpenChange, onDone, projectId, folders }: { open: boolean; onOpenChange: (o: boolean) => void; onDone: () => void; projectId?: string; folders?: string[] }) {
  const L = useL();
  const { data: cols } = useSWR(open && !projectId ? "/api/v1/knowledge/collections" : null);
  const [files, setFiles] = useState<File[]>([]);
  const [folder, setFolder] = useState("");
  const [kb, setKb] = useState(true);
  const [col, setCol] = useState("");
  const [prog, setProg] = useState<{ done: number; errors: string[] } | null>(null);
  async function go() {
    setProg({ done: 0, errors: [] });
    const errors: string[] = [];
    for (let i = 0; i < files.length; i++) {
      const fd = new FormData();
      fd.append("file", files[i]);
      fd.append("folder", folder);
      fd.append("add_to_knowledge", String(kb));
      if (projectId) fd.append("project_id", projectId);
      if (kb && col) fd.append("collection_id", col);
      try { await upload("/api/v1/files", fd); } catch (e: any) { errors.push(`${files[i].name}: ${e.message}`); }
      setProg({ done: i + 1, errors: [...errors] });
    }
    onDone();
    if (!errors.length) { setFiles([]); onOpenChange(false); setProg(null); }
  }
  return (
    <Modal open={open} onOpenChange={(o) => { onOpenChange(o); if (!o) { setProg(null); setFiles([]); } }} title={L("Subir archivos", "Upload files")}
      description={L("PDF, Word, PowerPoint, Excel, CSV, JSON, SQLite, Markdown, código, imágenes y audio. Máx. 500 MB por archivo.", "PDF, Word, PowerPoint, Excel, CSV, JSON, SQLite, Markdown, code, images and audio. Max 500 MB per file.")}>
      <div className="space-y-3">
        <label className="flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed border-border p-6 text-center text-sm text-muted hover:border-accent/50"
          onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); setFiles([...files, ...Array.from(e.dataTransfer.files)]); }}>
          <Upload className="h-6 w-6 text-accent" />
          {L("Arrastra aquí o haz clic para elegir", "Drop here or click to choose")}
          <input type="file" multiple className="hidden" onChange={(e) => setFiles([...files, ...Array.from(e.target.files ?? [])])} />
        </label>
        {files.length > 0 && <ul className="max-h-32 overflow-y-auto text-xs text-muted">{files.map((f, i) => <li key={i}>{f.name} · {bytes(f.size)}</li>)}</ul>}
        {!projectId && <Field label={L("Carpeta (opcional)", "Folder (optional)")} hint={L("Usa / para subcarpetas, p. ej. Tesis/Capítulo 2", "Use / for subfolders, e.g. Thesis/Chapter 2")}>
          <Input list="folders" value={folder} onChange={(e) => setFolder(e.target.value)} placeholder={L("(raíz)", "(root)")} />
          <datalist id="folders">{folders?.map((x) => <option key={x} value={x} />)}</datalist></Field>}
        <Switch checked={kb} onChange={setKb} label={projectId ? L("Indexar en los documentos del proyecto", "Index into the project documents") : L("Añadir a la base de conocimiento (el chat podrá buscar en ellos)", "Add to the knowledge base (the chat can search them)")} />
        {kb && !projectId && cols?.collections && <Field label={L("Colección", "Collection")}><Select value={col} onChange={(e) => setCol(e.target.value)}>
          <option value="">{L("Mis documentos", "My documents")}</option>{cols.collections.filter((c: any) => c.is_owner && !c.project_id && c.name !== "Mis documentos").map((c: any) => <option key={c.id} value={c.id}>{c.name}</option>)}</Select></Field>}
        {prog && <div className="space-y-1"><Progress value={(prog.done / Math.max(files.length, 1)) * 100} /><p className="text-xs text-muted">{prog.done}/{files.length}</p>{prog.errors.map((e) => <p key={e} className="text-xs text-danger">{e}</p>)}</div>}
        <div className="flex justify-end gap-2"><Button variant="ghost" onClick={() => onOpenChange(false)}>{L("Cancelar", "Cancel")}</Button>
          <Button variant="primary" disabled={!files.length || (prog !== null && prog.done < files.length)} onClick={go}><Upload className="h-4 w-4" />{L("Subir", "Upload")} {files.length || ""}</Button></div>
      </div>
    </Modal>
  );
}

