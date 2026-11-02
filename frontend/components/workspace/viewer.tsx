"use client";
// One open file of the workspace: code editor with line numbers (Tab indents, Ctrl+S saves, Ctrl+Enter runs), table
// view for CSV/Excel, previews for Markdown, HTML (CSP sandbox), PDF and images. Unsaved text lives in the page
// (drafts), so switching tabs never loses it.
import * as DM from "@radix-ui/react-dropdown-menu";
import { ChevronRight, Copy, Download, Ellipsis, ExternalLink, HardDriveUpload, Loader2, Play, RotateCcw, Save, TriangleAlert, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR from "swr";
import { Button, Notice, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { bytes } from "@/lib/format";
import { useL } from "@/lib/i18n";
import { useToast } from "@/lib/providers";
import { type Draft, IMG, PROSE, RUNNABLE, TABLE, baseName, ext, handleIndent, insertText, rawUrl } from "./common";

type Mode = "view" | "edit";

export function Viewer({ wid, wsName, path, version, draft, onDraft, onSaved, onRun, onReveal, onClose, saveRef }: {
  wid: string; wsName: string; path: string; version: number; draft?: Draft;
  onDraft: (path: string, d: Draft | null) => void; onSaved: (path: string) => void; onRun: (cmd: string) => void;
  onReveal: (dir: string) => void; onClose: () => void; saveRef: React.MutableRefObject<(() => Promise<void>) | null>;
}) {
  const L = useL();
  const { push } = useToast();
  const e = ext(path);
  const isSheet = ["xlsx", "xlsm"].includes(e);
  const isTable = TABLE.includes(e);
  const isPdf = e === "pdf";
  const isImg = IMG.includes(e) && e !== "svg";
  const isHtml = e === "html" || e === "htm";
  const hasPreview = isTable || isHtml || e === "md" || e === "svg";
  const q = `path=${encodeURIComponent(path)}&v=${version}`;
  // keepPreviousData: a refresh (new version) does not blank the editor while it reloads
  const { data: f, error, mutate } = useSWR(!isPdf && !isImg && !isSheet ? `/api/v1/workspaces/${wid}/file?${q}` : null, { keepPreviousData: true });
  const { data: tbl, error: tblErr } = useSWR(isTable ? `/api/v1/workspaces/${wid}/table?${q}` : null, { keepPreviousData: true });
  const [mode, setMode] = useState<Mode>(hasPreview && !draft ? "view" : "edit");
  const [sheet, setSheet] = useState(0);
  const [saving, setSaving] = useState(false);
  const raw = rawUrl(wid, path);
  const text = draft?.text ?? f?.content ?? "";
  const dirty = !!draft;
  const conflict = dirty && f && draft.mtime !== undefined && f.mtime !== draft.mtime;
  const readOnly = !!f?.truncated;

  async function save() {
    if (!draft || readOnly) return;
    setSaving(true);
    try {
      await api(`/api/v1/workspaces/${wid}/file`, { method: "PUT", json: { path, content: draft.text } });
      // show the saved text at once (mtime unknown until the reload that onSaved triggers)
      await mutate({ ...f, content: draft.text, mtime: undefined }, { revalidate: false });
      onSaved(path);
      push("ok", L(`Guardado ${baseName(path)}`, `Saved ${baseName(path)}`));
    } catch (er: any) { push("error", er.message); } finally { setSaving(false); }
  }
  saveRef.current = save;
  async function runIt() { if (dirty) await save(); onRun(RUNNABLE[e](path)); }
  const change = (v: string) => onDraft(path, v === f?.content ? null : { text: v, mtime: draft?.mtime ?? f?.mtime });

  const segs = path.split("/");
  const seg = (a: Mode, label: string) => (
    <button type="button" onClick={() => setMode(a)} className={cx("rounded-md px-2 py-0.5 text-[11.5px] transition", mode === a ? "bg-surface text-fg shadow-sm" : "text-muted hover:text-fg")}>{label}</button>
  );
  const notFound = error?.status === 404 || tblErr?.status === 404;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex min-h-[40px] flex-wrap items-center gap-1.5 border-b border-border px-3 py-1">
        <nav aria-label={L("Ruta", "Path")} className="mr-auto flex min-w-0 items-center gap-0.5 overflow-hidden text-xs text-muted">
          <button type="button" className="shrink-0 rounded px-1 py-0.5 hover:bg-elevated hover:text-fg" onClick={() => onReveal("")} title={L("Raíz del workspace", "Workspace root")}>{wsName}</button>
          {segs.map((s, i) => (
            <span key={i} className="flex min-w-0 items-center gap-0.5">
              <ChevronRight className="h-3 w-3 shrink-0 text-subtle" />
              {i < segs.length - 1
                ? <button type="button" className="truncate rounded px-1 py-0.5 hover:bg-elevated hover:text-fg" onClick={() => onReveal(segs.slice(0, i + 1).join("/"))}>{s}</button>
                : <span className="truncate px-1 font-medium text-fg" title={path}>{s}{dirty && <span className="ml-1 text-accent" title={L("Cambios sin guardar", "Unsaved changes")}>●</span>}</span>}
            </span>
          ))}
        </nav>
        {hasPreview && !isSheet && f?.text !== false && (
          <div className="flex rounded-lg bg-elevated p-0.5" role="group" aria-label={L("Modo", "Mode")}>
            {seg("view", isTable ? L("Tabla", "Table") : L("Vista", "Preview"))}
            {seg("edit", e === "md" ? L("Editar", "Edit") : isTable ? L("Texto", "Text") : L("Código", "Code"))}
          </div>
        )}
        {f?.text && (mode === "edit" || !hasPreview) && (
          <Button size="sm" variant={dirty ? "primary" : "ghost"} disabled={!dirty || readOnly} loading={saving} onClick={save} title={L("Guardar (Ctrl+S)", "Save (Ctrl+S)")}>
            <Save className="h-3.5 w-3.5" /><span className="hidden sm:inline">{L("Guardar", "Save")}</span>
          </Button>
        )}
        {RUNNABLE[e] && <Button size="sm" variant="outline" onClick={runIt} title={L("Ejecutar en el terminal (Ctrl+Enter)", "Run in the terminal (Ctrl+Enter)")}><Play className="h-3.5 w-3.5" />{L("Ejecutar", "Run")}</Button>}
        <FileMenu wid={wid} path={path} dirty={dirty} onReload={() => { onDraft(path, null); mutate(); }} />
      </div>
      {conflict && (
        <div className="flex flex-wrap items-center gap-2 border-b border-warn/30 bg-warn/10 px-3 py-1.5 text-xs text-warn">
          <TriangleAlert className="h-3.5 w-3.5 shrink-0" />
          <span className="mr-auto">{L("El archivo ha cambiado en el workspace (el asistente o un comando) mientras lo editabas.", "The file changed in the workspace (the assistant or a command) while you were editing it.")}</span>
          <Button size="sm" variant="ghost" className="!h-7" onClick={() => onDraft(path, null)}>{L("Ver la versión nueva", "Show the new version")}</Button>
          <Button size="sm" variant="outline" className="!h-7" onClick={() => onDraft(path, { text: draft!.text, mtime: f.mtime })}>{L("Mantener mis cambios", "Keep my changes")}</Button>
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-hidden">
        {notFound ? (
          <div className="grid h-full place-items-center p-6"><div className="space-y-3 text-center">
            <p className="text-sm text-muted">{L("Este archivo ya no existe (se borró o se movió).", "This file no longer exists (deleted or moved).")}</p>
            <Button size="sm" variant="outline" onClick={onClose}><X className="h-3.5 w-3.5" />{L("Cerrar pestaña", "Close tab")}</Button>
          </div></div>
        ) : isImg ? <div className="grid h-full place-items-center overflow-auto bg-[repeating-conic-gradient(rgb(var(--elevated))_0%_25%,transparent_0%_50%)] bg-[length:16px_16px] p-4"><img src={`${raw}&v=${version}`} alt={path} className="max-h-full max-w-full rounded-lg border border-border bg-white object-contain" /></div>
          : isPdf ? <iframe title={path} src={`${raw}&v=${version}`} className="h-full w-full" />
          : isHtml && mode === "view" ? <iframe title={path} sandbox="allow-scripts" src={`${raw}&v=${version}${dirty ? "" : `&t=${f?.mtime ?? ""}`}`} className="h-full w-full bg-white" />
          : e === "svg" && mode === "view" ? <div className="grid h-full place-items-center overflow-auto p-4"><img src={`${raw}&v=${version}&t=${f?.mtime ?? ""}`} alt={path} className="max-h-full max-w-full rounded-lg border border-border bg-white" /></div>
          : isTable && mode === "view" ? <TableView tbl={tbl} error={tblErr} sheet={sheet} setSheet={setSheet} />
          : !f ? (error ? <div className="p-4"><Notice tone="danger">{error.message}</Notice></div> : <Loader2 className="m-4 h-4 w-4 animate-spin text-muted" />)
          : f.text ? (e === "md" && mode === "view" ? (
              <div className="h-full overflow-auto p-5"><div className="prose-chat mx-auto max-w-3xl"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div></div>)
            : <div className="flex h-full min-h-0 flex-col">
                {readOnly && <div className="px-3 pt-2"><Notice tone="warn">{L("Archivo grande: solo se muestran los primeros 2 MB, en modo lectura. Edítalo con un comando o descárgalo.", "Large file: only the first 2 MB are shown, read-only. Edit it with a command or download it.")}</Notice></div>}
                <CodeArea value={text} path={path} readOnly={readOnly} onChange={change} onSave={save} onRun={RUNNABLE[e] ? runIt : undefined} />
              </div>)
          : <div className="grid h-full place-items-center p-6"><div className="max-w-sm space-y-3 text-center text-sm text-muted">
              <p>{L(`Archivo binario (${f.mime}, ${bytes(f.size)}). No se puede mostrar aquí: descárgalo, ábrelo en otra pestaña o trabaja con él desde el terminal o el asistente.`, `Binary file (${f.mime}, ${bytes(f.size)}). It cannot be shown here: download it, open it in another tab or work with it from the terminal or the assistant.`)}</p>
              <div className="flex justify-center gap-2">
                <a href={rawUrl(wid, path, true)}><Button size="sm" variant="outline"><Download className="h-3.5 w-3.5" />{L("Descargar", "Download")}</Button></a>
                <a href={raw} target="_blank" rel="noreferrer"><Button size="sm" variant="ghost"><ExternalLink className="h-3.5 w-3.5" />{L("Abrir", "Open")}</Button></a>
              </div>
            </div></div>}
      </div>
    </div>
  );
}

function FileMenu({ wid, path, dirty, onReload }: { wid: string; path: string; dirty: boolean; onReload: () => void }) {
  const L = useL();
  const { push } = useToast();
  const item = "flex cursor-pointer items-center gap-2 rounded-lg px-2.5 py-1.5 text-[13px] text-fg outline-none data-[highlighted]:bg-elevated";
  return (
    <DM.Root>
      <DM.Trigger asChild><Button size="sm" variant="ghost" aria-label={L("Más acciones", "More actions")} className="!px-2"><Ellipsis className="h-4 w-4" /></Button></DM.Trigger>
      <DM.Portal>
        <DM.Content align="end" sideOffset={4} className="z-[90] min-w-[230px] rounded-xl border border-border bg-surface p-1 shadow-card">
          <DM.Item className={item} onSelect={() => window.open(rawUrl(wid, path), "_blank", "noopener")}><ExternalLink className="h-3.5 w-3.5 text-muted" />{L("Abrir en una pestaña nueva", "Open in a new browser tab")}</DM.Item>
          <DM.Item className={item} onSelect={() => { window.location.href = rawUrl(wid, path, true); }}><Download className="h-3.5 w-3.5 text-muted" />{L("Descargar", "Download")}</DM.Item>
          <DM.Item className={item} onSelect={async () => { try { const r = await api(`/api/v1/workspaces/${wid}/export`, { method: "POST", json: { path } }); push("ok", L(`Copiado a Mis archivos → ${r.file.folder}`, `Copied to My files → ${r.file.folder}`)); } catch (er: any) { push("error", er.message); } }}>
            <HardDriveUpload className="h-3.5 w-3.5 text-muted" />{L("Guardar copia en Mis archivos", "Save a copy to My files")}</DM.Item>
          <DM.Item className={item} onSelect={() => { navigator.clipboard?.writeText(path).then(() => push("ok", L("Ruta copiada", "Path copied")), () => {}); }}><Copy className="h-3.5 w-3.5 text-muted" />{L("Copiar ruta", "Copy path")}</DM.Item>
          <DM.Separator className="my-1 h-px bg-border" />
          <DM.Item className={item} onSelect={onReload}><RotateCcw className="h-3.5 w-3.5 text-muted" />{dirty ? L("Descartar cambios y recargar", "Discard changes and reload") : L("Recargar del workspace", "Reload from the workspace")}</DM.Item>
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}

function TableView({ tbl, error, sheet, setSheet }: { tbl: any; error: any; sheet: number; setSheet: (i: number) => void }) {
  const L = useL();
  if (error) return <div className="p-4"><Notice tone="danger">{error.message}</Notice></div>;
  if (!tbl) return <Loader2 className="m-4 h-4 w-4 animate-spin text-muted" />;
  const rows: string[][] = tbl.sheets[sheet]?.rows ?? [];
  const [head, ...body] = rows;
  return (
    <div className="flex h-full min-h-0 flex-col">
      {tbl.sheets.length > 1 && (
        <div className="flex gap-1 overflow-x-auto border-b border-border px-2 py-1">
          {tbl.sheets.map((s: any, i: number) => <button key={i} type="button" onClick={() => setSheet(i)} className={cx("whitespace-nowrap rounded-md px-2.5 py-1 text-xs", i === sheet ? "bg-accent/12 font-medium text-fg" : "text-muted hover:bg-elevated")}>{s.name}</button>)}
        </div>
      )}
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="min-w-full border-separate border-spacing-0 text-xs">
          {head && <thead><tr>
            <th className="sticky left-0 top-0 z-20 border-b border-r border-border bg-elevated px-2 py-1.5 text-right font-normal text-subtle">#</th>
            {head.map((c, j) => <th key={j} className="sticky top-0 z-10 whitespace-nowrap border-b border-border bg-elevated px-2.5 py-1.5 text-left font-semibold">{c}</th>)}
          </tr></thead>}
          <tbody>{body.map((r, i) => (
            <tr key={i} className="hover:bg-elevated/50">
              <td className="sticky left-0 border-b border-r border-border/60 bg-surface px-2 py-1 text-right tabular-nums text-subtle">{i + 1}</td>
              {r.map((c, j) => <td key={j} className="whitespace-nowrap border-b border-border/40 px-2.5 py-1">{c}</td>)}
            </tr>))}</tbody>
        </table>
        <p className="px-3 py-2 text-[11px] text-subtle">{L("Vista previa de las primeras 200 filas. Para trabajar con todo el archivo, usa el terminal o pídeselo al asistente.", "Preview of the first 200 rows. To work with the whole file, use the terminal or ask the assistant.")}</p>
      </div>
    </div>
  );
}

/** Plain-textarea code editor: line numbers for code, wrapped lines for prose, Tab indents, Enter keeps indentation. */
function CodeArea({ value, path, readOnly, onChange, onSave, onRun }: {
  value: string; path: string; readOnly: boolean; onChange: (v: string) => void; onSave: () => void; onRun?: () => void;
}) {
  const L = useL();
  const e = ext(path);
  const wrap = PROSE.includes(e);
  const unit = ["py", "java", "c", "cpp", "rs", "go", "php", "sh", "tex"].includes(e) ? "    " : "  ";
  const gutter = useRef<HTMLDivElement>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const lines = useMemo(() => (wrap ? 0 : value.split("\n").length), [value, wrap]);
  const nums = useMemo(() => Array.from({ length: lines }, (_, i) => i + 1).join("\n"), [lines]);
  useEffect(() => { if (gutter.current && area.current) gutter.current.scrollTop = area.current.scrollTop; }, [nums]);
  return (
    <div className="flex min-h-0 flex-1">
      {!wrap && <div ref={gutter} aria-hidden className="shrink-0 select-none overflow-hidden border-r border-border/60 bg-elevated/40 py-3 pl-2 pr-2 text-right font-mono text-[12.5px] leading-[1.6] text-subtle">
        <pre className="pb-10">{nums}</pre></div>}
      <textarea ref={area} spellCheck={false} wrap={wrap ? "soft" : "off"} readOnly={readOnly} value={value} aria-label={L(`Contenido de ${path}`, `Contents of ${path}`)}
        className={cx("min-h-0 flex-1 resize-none bg-bg p-3 font-mono text-[12.5px] leading-[1.6] text-fg outline-none", !wrap && "whitespace-pre")}
        onScroll={(ev) => { if (gutter.current) gutter.current.scrollTop = ev.currentTarget.scrollTop; }}
        onChange={(ev) => onChange(ev.target.value)}
        onKeyDown={(ev) => {
          if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "s") { ev.preventDefault(); ev.stopPropagation(); onSave(); return; }
          if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter" && onRun) { ev.preventDefault(); onRun(); return; }
          if (readOnly) return;
          if (handleIndent(ev, unit)) return;
          if (ev.key === "Enter" && !ev.shiftKey && !ev.nativeEvent.isComposing && !wrap) {
            const el = ev.currentTarget;
            const line = el.value.slice(el.value.lastIndexOf("\n", el.selectionStart - 1) + 1, el.selectionStart);
            const indent = line.match(/^[ \t]*/)?.[0] ?? "";
            if (indent) { ev.preventDefault(); insertText(el, `\n${indent}`); }
          }
        }} />
    </div>
  );
}
