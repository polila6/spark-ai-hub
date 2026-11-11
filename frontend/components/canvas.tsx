"use client";
// Canvas: a document or code file next to the chat. The assistant writes it (canvas_write / canvas_edit, deep research
// reports); the user reads, edits, restores versions and exports it (Markdown, Word, PDF, HTML, source file).
import { Check, Copy, Download, FileText, History, Loader2, Pencil, Save, Send, X } from "lucide-react";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR from "swr";
import { api, postBlob } from "@/lib/api";
import { dt } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useToast } from "@/lib/providers";
import { Badge, Button, Select, Textarea, cx } from "./ui";

const EXT: Record<string, string> = { md: "md", docx: "docx", pdf: "pdf", html: "html", odt: "odt" };

async function download(id: string, format: string, title: string, code?: string) {
  const blob = await postBlob(`/api/v1/canvas/${id}/export`, { format });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${title.replace(/[^\p{L}\p{N}_-]+/gu, "_").slice(0, 80) || "documento"}.${code ?? EXT[format]}`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 5000);
}

export function CanvasPanel({ id, version, onClose, onAsk, busy }: { id: string; version?: number; onClose: () => void; onAsk: (text: string) => void; busy?: boolean }) {
  const L = useL();
  const { lang } = useI18n();
  const { push } = useToast();
  const { data: d, mutate } = useSWR(`/api/v1/canvas/${id}?v=${version ?? 0}`);
  const [edit, setEdit] = useState(false);
  const [draft, setDraft] = useState("");
  const [ask, setAsk] = useState("");
  const [working, setWorking] = useState("");
  const [copied, setCopied] = useState(false);
  useEffect(() => { if (d && !edit) setDraft(d.content); }, [d, edit]);
  if (!d) return <div className="grid h-full place-items-center"><Loader2 className="h-5 w-5 animate-spin text-muted" /></div>;
  const isCode = d.kind === "code";
  const codeExt = isCode ? ({ python: "py", javascript: "js", typescript: "ts", bash: "sh", sql: "sql", latex: "tex", html: "html", r: "r", cpp: "cpp", c: "c", java: "java", go: "go", rust: "rs", json: "json", yaml: "yaml" } as Record<string, string>)[d.language ?? ""] ?? "txt" : undefined;
  async function save() {
    setWorking("save");
    try { await api(`/api/v1/canvas/${id}`, { method: "PUT", json: { content: draft, base_version: d.version } }); await mutate(); setEdit(false); push("ok", L("Guardado como nueva versión", "Saved as a new version")); }
    catch (e: any) { push("error", e.message); } finally { setWorking(""); }
  }
  async function exp(format: string) {
    setWorking(format);
    try { await download(id, format, d.title, format === "code" ? codeExt : undefined); } catch (e: any) { push("error", e.message); } finally { setWorking(""); }
  }
  async function saveToDrive(format: string) {
    setWorking("drive");
    try { const r = await api(`/api/v1/canvas/${id}/export`, { method: "POST", json: { format, save: true } }); push("ok", L(`Guardado en Mis archivos → Lienzos: ${r.file?.filename}`, `Saved to My files → Lienzos: ${r.file?.filename}`)); }
    catch (e: any) { push("error", e.message); } finally { setWorking(""); }
  }
  async function restore(v: number) {
    try { await api(`/api/v1/canvas/${id}/restore/${v}`, { method: "POST" }); await mutate(); push("ok", L(`Versión ${v} restaurada (como versión nueva)`, `Version ${v} restored (as a new version)`)); } catch (e: any) { push("error", e.message); }
  }
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2 pt-[max(0.5rem,env(safe-area-inset-top))] lg:pt-2">
        <FileText className="h-4 w-4 shrink-0 text-accent" />
        <span className="min-w-0 flex-1 truncate text-sm font-semibold" title={d.title}>{d.title}</span>
        <Badge>v{d.version}</Badge>
        {d.versions?.length > 1 && (
          <Select className="h-9 w-auto text-xs lg:h-7" aria-label={L("Versiones", "Versions")} value="" onChange={(e) => e.target.value && restore(Number(e.target.value))}>
            <option value="">{L("Versiones…", "Versions…")}</option>
            {[...d.versions].reverse().filter((v: any) => v.version !== d.version).map((v: any) => <option key={v.version} value={v.version}>v{v.version} · {v.author === "user" ? L("tú", "you") : L("asistente", "assistant")} · {dt(v.at, lang)}</option>)}
          </Select>
        )}
        <Button size="icon" variant="ghost" aria-label={L("Cerrar lienzo", "Close canvas")} onClick={onClose}><X className="h-4 w-4" /></Button>
      </div>
      <div className="flex items-center gap-1 overflow-x-auto whitespace-nowrap border-b border-border px-2 py-1.5 text-xs sm:px-3 lg:flex-wrap lg:overflow-visible [&>*]:shrink-0 [&_button]:!h-9 lg:[&_button]:!h-8">
        {edit ? (
          <>
            <Button size="sm" variant="primary" loading={working === "save"} onClick={save}><Save className="h-3.5 w-3.5" />{L("Guardar", "Save")}</Button>
            <Button size="sm" variant="ghost" onClick={() => { setEdit(false); setDraft(d.content); }}>{L("Cancelar", "Cancel")}</Button>
          </>
        ) : <Button size="sm" variant="ghost" onClick={() => setEdit(true)}><Pencil className="h-3.5 w-3.5" />{L("Editar", "Edit")}</Button>}
        <Button size="sm" variant="ghost" onClick={async () => { await navigator.clipboard.writeText(d.content); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>{copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}{L("Copiar", "Copy")}</Button>
        <span className="mx-1 h-4 w-px bg-border" />
        {isCode ? <Button size="sm" variant="ghost" loading={working === "code"} onClick={() => exp("code")}><Download className="h-3.5 w-3.5" />.{codeExt}</Button>
          : ["docx", "pdf", "md", "html"].map((f) => <Button key={f} size="sm" variant="ghost" loading={working === f} onClick={() => exp(f)}><Download className="h-3.5 w-3.5" />{f === "docx" ? "Word" : f.toUpperCase()}</Button>)}
        <Button size="sm" variant="ghost" loading={working === "drive"} onClick={() => saveToDrive(isCode ? "code" : "docx")} title={L("Guardar una copia en Mis archivos", "Save a copy to My files")}><History className="h-3.5 w-3.5" />{L("A Mis archivos", "To My files")}</Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3 sm:p-4">
        {edit ? <Textarea className="h-full min-h-[50dvh] font-mono text-base lg:text-xs" value={draft} onChange={(e) => setDraft(e.target.value)} />
          : isCode ? <pre className="overflow-x-auto rounded-lg bg-elevated p-3 font-mono text-[12.5px] lg:text-xs leading-relaxed">{d.content}</pre>
            : <div className="prose-chat max-w-none"><ReactMarkdown remarkPlugins={[remarkGfm]}>{d.content}</ReactMarkdown></div>}
      </div>
      <form className="flex gap-2 border-t border-border p-2" onSubmit={(e) => { e.preventDefault(); if (ask.trim()) { onAsk(L(`En el lienzo «${d.title}»: ${ask.trim()}`, `In the canvas “${d.title}”: ${ask.trim()}`)); setAsk(""); } }}>
        <input className={cx("h-11 min-w-0 flex-1 rounded-lg border border-border bg-bg px-3 text-base lg:h-9 lg:text-sm outline-none focus:ring-2 focus:ring-accent/40")} value={ask} onChange={(e) => setAsk(e.target.value)}
          placeholder={L("Pide un cambio: «hazlo más corto», «añade una tabla»…", "Ask for a change: “make it shorter”, “add a table”…")} />
        <Button type="submit" size="icon" variant="primary" disabled={!ask.trim() || busy} aria-label={L("Enviar", "Send")}><Send className="h-4 w-4" /></Button>
      </form>
    </div>
  );
}

export function CanvasChip({ canvas, onOpen }: { canvas: any; onOpen: (id: string, v?: number) => void }) {
  const L = useL();
  return (
    <div className="flex items-center gap-2 border-t border-border/60 px-3 py-2">
      <FileText className="h-3.5 w-3.5 text-accent" /><span className="truncate font-medium text-fg">{canvas.title}</span><Badge>v{canvas.version}</Badge>
      <Button size="sm" variant="outline" className="ml-auto" onClick={() => onOpen(canvas.id, canvas.version)}>{L("Abrir lienzo", "Open canvas")}</Button>
    </div>
  );
}
