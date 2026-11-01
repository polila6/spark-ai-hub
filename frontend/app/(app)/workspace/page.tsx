"use client";
// Workspace: a persistent remote folder per user with files, an editor with previews, a sandboxed terminal and an
// assistant that writes, runs and fixes code in it (workspace tools ws_*). Commands run without network.
import {
  ChevronDown, ChevronRight, Download, File, FileCode2, FilePlus2, FolderOpen, FolderPlus, HardDriveDownload, Loader2,
  MessageSquarePlus, Pencil, Play, Plus, RefreshCw, Save, Send, Square, SquareTerminal, Trash2, Upload, Wrench,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR from "swr";
import { Guide } from "@/components/guide";
import { Badge, Button, Card, Input, Modal, Notice, Select, Textarea, cx } from "@/components/ui";
import { api, streamGet, streamPost, upload } from "@/lib/api";
import { bytes } from "@/lib/format";
import { useL } from "@/lib/i18n";
import { useToast } from "@/lib/providers";

type Entry = { path: string; type: "file" | "dir" | "link"; size: number; collapsed?: boolean };
type Run = { id?: string; status?: string; command: string; stdout?: string; stderr?: string; exit_code?: number; timed_out?: boolean; seconds?: number; changed?: string[]; running?: boolean };
type ChatMsg = { role: string; content: string; tools?: any[]; pending?: boolean };

const ext = (p: string) => (p.split(".").pop() ?? "").toLowerCase();
const IMG = ["png", "jpg", "jpeg", "gif", "webp", "bmp", "svg"];
const RUNNABLE: Record<string, (p: string) => string> = { py: (p) => `python "${p}"`, sh: (p) => `bash "${p}"`, js: (p) => `node "${p}"`, mjs: (p) => `node "${p}"` };

function Tree({ entries, selected, onOpen }: { entries: Entry[]; selected: string | null; onOpen: (e: Entry) => void }) {
  const [closed, setClosed] = useState<Set<string>>(new Set());
  useEffect(() => { setClosed((c) => { const n = new Set(c); entries.filter((e) => e.collapsed).forEach((e) => n.add(e.path)); return n; }); }, [entries]);
  const visible = entries.filter((e) => { const parts = e.path.split("/"); for (let i = 1; i < parts.length; i++) if (closed.has(parts.slice(0, i).join("/"))) return false; return true; });
  return (
    <ul className="space-y-px text-[13px]">
      {visible.map((e) => {
        const depth = e.path.split("/").length - 1;
        const name = e.path.split("/").pop();
        const open = !closed.has(e.path);
        return (
          <li key={e.path}>
            <button type="button" style={{ paddingLeft: 6 + depth * 14 }} onClick={() => (e.type === "dir" ? setClosed((c) => { const n = new Set(c); open ? n.add(e.path) : n.delete(e.path); return n; }) : onOpen(e))}
              className={cx("flex w-full items-center gap-1.5 rounded-md py-1 pr-2 text-left hover:bg-elevated", selected === e.path && "bg-accent/12 text-fg")} title={e.path}>
              {e.type === "dir" ? (open ? <ChevronDown className="h-3 w-3 shrink-0" /> : <ChevronRight className="h-3 w-3 shrink-0" />) : <span className="w-3" />}
              {e.type === "dir" ? <FolderOpen className="h-3.5 w-3.5 shrink-0 text-accent" /> : RUNNABLE[ext(e.path)] ? <FileCode2 className="h-3.5 w-3.5 shrink-0 text-info" /> : <File className="h-3.5 w-3.5 shrink-0 text-muted" />}
              <span className={cx("truncate", e.type === "link" && "italic text-subtle")}>{name}</span>
              {e.type === "file" && <span className="ml-auto shrink-0 text-[10px] text-subtle">{bytes(e.size)}</span>}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

function Viewer({ wid, path, version, onRun, onSaved }: { wid: string; path: string; version: number; onRun: (cmd: string) => void; onSaved: () => void }) {
  const L = useL();
  const { push } = useToast();
  const e = ext(path);
  const raw = `/api/v1/workspaces/${wid}/raw?path=${encodeURIComponent(path)}`;
  const q = `path=${encodeURIComponent(path)}&v=${version}`;
  const isTable = ["csv", "tsv", "xlsx", "xlsm"].includes(e);
  const isMedia = IMG.includes(e) || e === "pdf" || e === "html" || e === "htm";
  const { data: f, mutate } = useSWR(!isMedia && !["xlsx", "xlsm"].includes(e) ? `/api/v1/workspaces/${wid}/file?${q}` : null);
  const { data: tbl } = useSWR(isTable ? `/api/v1/workspaces/${wid}/table?${q}` : null);
  const [draft, setDraft] = useState<string | null>(null);
  const [view, setView] = useState<"table" | "text">("table");
  const [sheet, setSheet] = useState(0);
  const [nonce, setNonce] = useState(0);
  useEffect(() => { setDraft(null); setNonce((n) => n + 1); }, [path, version]);
  useEffect(() => { setSheet(0); }, [path]);
  const dirty = draft !== null && draft !== f?.content;
  async function save() {
    try { await api(`/api/v1/workspaces/${wid}/file`, { method: "PUT", json: { path, content: draft ?? "" } }); await mutate(); setDraft(null); push("ok", L("Guardado", "Saved")); onSaved(); } catch (er: any) { push("error", er.message); }
  }
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-1 border-b border-border px-3 py-1.5 text-xs">
        <span className="mr-auto truncate font-mono text-[12px] font-medium" title={path}>{path}{dirty && " •"}</span>
        {isTable && <Select className="!h-7 !w-auto text-xs" value={view} onChange={(ev) => setView(ev.target.value as any)}><option value="table">{L("Tabla", "Table")}</option>{!["xlsx", "xlsm"].includes(e) && <option value="text">{L("Texto", "Text")}</option>}</Select>}
        {f?.text && <Button size="sm" variant={dirty ? "primary" : "ghost"} disabled={!dirty} onClick={save}><Save className="h-3.5 w-3.5" />{L("Guardar", "Save")}</Button>}
        {RUNNABLE[e] && <Button size="sm" variant="outline" onClick={async () => { if (dirty) await save(); onRun(RUNNABLE[e](path)); }}><Play className="h-3.5 w-3.5" />{L("Ejecutar", "Run")}</Button>}
        <a href={`${raw}&download=true`}><Button size="sm" variant="ghost"><Download className="h-3.5 w-3.5" /></Button></a>
        <Button size="sm" variant="ghost" title={L("Guardar una copia en Mis archivos", "Save a copy to My files")} onClick={async () => { try { const r = await api(`/api/v1/workspaces/${wid}/export`, { method: "POST", json: { path } }); push("ok", L(`Copiado a Mis archivos → ${r.file.folder}`, `Copied to My files → ${r.file.folder}`)); } catch (er: any) { push("error", er.message); } }}><HardDriveDownload className="h-3.5 w-3.5" /></Button>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        {IMG.includes(e) ? <div className="grid place-items-center p-4"><img src={`${raw}&n=${nonce}`} alt={path} className="max-h-[70vh] max-w-full rounded-lg border border-border bg-white" /></div>
          : e === "pdf" ? <iframe title={path} src={`${raw}&n=${nonce}`} className="h-full min-h-[60vh] w-full" />
          : e === "html" || e === "htm" ? <iframe title={path} sandbox="allow-scripts" src={`${raw}&n=${nonce}`} className="h-full min-h-[60vh] w-full bg-white" />
          : isTable && view === "table" ? (
            !tbl ? <Loader2 className="m-4 h-4 w-4 animate-spin" /> : (
              <div className="p-2">
                {tbl.sheets.length > 1 && <div className="mb-2 flex gap-1">{tbl.sheets.map((s: any, i: number) => <Button key={i} size="sm" variant={i === sheet ? "primary" : "ghost"} onClick={() => setSheet(i)}>{s.name}</Button>)}</div>}
                <table className="w-full border-collapse text-xs"><tbody>
                  {(tbl.sheets[sheet]?.rows ?? []).map((r: string[], i: number) => <tr key={i} className={i === 0 ? "bg-elevated font-semibold" : "border-t border-border/50"}>{r.map((c, j) => <td key={j} className="whitespace-nowrap px-2 py-1">{c}</td>)}</tr>)}
                </tbody></table>
                <p className="mt-2 text-[11px] text-subtle">{L("Vista previa de las primeras 200 filas", "Preview of the first 200 rows")}</p>
              </div>))
          : !f ? <Loader2 className="m-4 h-4 w-4 animate-spin" />
          : f.text ? (e === "md" && draft === null ? (
              <div className="p-4"><div className="prose-chat max-w-none"><ReactMarkdown remarkPlugins={[remarkGfm]}>{f.content}</ReactMarkdown></div>
                <Button size="sm" variant="ghost" className="mt-3" onClick={() => setDraft(f.content)}><Pencil className="h-3.5 w-3.5" />{L("Editar", "Edit")}</Button></div>)
            : <textarea spellCheck={false} className="h-full min-h-[50vh] w-full resize-none bg-bg p-3 font-mono text-[12.5px] leading-relaxed outline-none" value={draft ?? f.content}
                onChange={(ev) => setDraft(ev.target.value)} onKeyDown={(ev) => { if ((ev.ctrlKey || ev.metaKey) && ev.key === "s") { ev.preventDefault(); save(); } }} />)
          : <Notice tone="muted">{L(`Archivo binario (${f.mime}, ${bytes(f.size)}). Descárgalo o ábrelo con un comando en el terminal.`, `Binary file (${f.mime}, ${bytes(f.size)}). Download it or open it with a terminal command.`)}</Notice>}
        {f?.truncated && <Notice tone="warn">{L("Archivo grande: solo se muestran los primeros 2 MB (no lo guardes desde aquí).", "Large file: only the first 2 MB are shown (do not save it from here).")}</Notice>}
      </div>
    </div>
  );
}

function Terminal({ runs, busy, onRun, onOpen, onClear }: { runs: Run[]; busy: boolean; onRun: (c: string) => void; onOpen: (p: string) => void; onClear: () => void }) {
  const L = useL();
  const [cmd, setCmd] = useState("");
  const [hist, setHist] = useState(-1);
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [runs]);
  const past = runs.map((r) => r.command).reverse();
  return (
    <div className="flex h-full min-h-0 flex-col bg-[#0d1117] text-[12px] text-[#c9d1d9]">
      <div className="min-h-0 flex-1 overflow-y-auto p-3 font-mono">
        {runs.length === 0 && <p className="text-[#8b949e]">{L("Terminal del workspace (carpeta actual = raíz del workspace, sin internet). Prueba: ls -la · python script.py · pandoc informe.md -o informe.pdf", "Workspace terminal (current folder = workspace root, no internet). Try: ls -la · python script.py · pandoc report.md -o report.pdf")}</p>}
        {runs.map((r, i) => (
          <div key={i} className="mb-2">
            <div className="text-[#7ee787]">$ {r.command}</div>
            {r.running ? <div className="flex items-center gap-1 text-[#8b949e]"><Loader2 className="h-3 w-3 animate-spin" />{L("ejecutando…", "running…")}</div> : <>
              {r.stdout && <pre className="whitespace-pre-wrap break-all">{r.stdout}</pre>}
              {r.stderr && <pre className="whitespace-pre-wrap break-all text-[#ff7b72]">{r.stderr}</pre>}
              <div className="text-[10px] text-[#8b949e]">{r.timed_out ? L("tiempo agotado", "timed out") : `exit ${r.exit_code}`} · {r.seconds?.toFixed(1)} s
                {r.changed && r.changed.length > 0 && <> · {L("cambiados", "changed")}: {r.changed.slice(0, 8).map((p) => <button key={p} className="mx-0.5 underline hover:text-white" onClick={() => onOpen(p)}>{p}</button>)}</>}</div></>}
          </div>))}
        <div ref={end} />
      </div>
      <form className="flex items-center gap-2 border-t border-[#30363d] px-3 py-2" onSubmit={(e) => { e.preventDefault(); if (cmd.trim() && !busy) { onRun(cmd.trim()); setCmd(""); setHist(-1); } }}>
        <span className="font-mono text-[#7ee787]">$</span>
        <input className="min-w-0 flex-1 bg-transparent font-mono outline-none placeholder:text-[#6e7681]" value={cmd} disabled={busy} placeholder={busy ? L("esperando al comando anterior…", "waiting for the previous command…") : "python script.py"}
          onChange={(e) => setCmd(e.target.value)} onKeyDown={(e) => {
            if (e.key === "ArrowUp" && past.length) { e.preventDefault(); const h = Math.min(past.length - 1, hist + 1); setHist(h); setCmd(past[h]); }
            if (e.key === "ArrowDown") { e.preventDefault(); const h = hist - 1; setHist(h); setCmd(h >= 0 ? past[h] : ""); } }} />
        <Button type="submit" size="sm" variant="ghost" disabled={busy || !cmd.trim()} className="text-[#c9d1d9]"><Play className="h-3.5 w-3.5" /></Button>
        {runs.length > 0 && !busy && <Button type="button" size="sm" variant="ghost" className="text-[#8b949e]" title={L("Limpiar el terminal", "Clear the terminal")} onClick={onClear}><Trash2 className="h-3.5 w-3.5" /></Button>}
      </form>
    </div>
  );
}

function ToolLine({ t, onOpen }: { t: any; onOpen: (p: string) => void }) {
  const L = useL();
  const changed: string[] = t.workspace?.changed ?? [];
  return (
    <div className="my-1.5 rounded-lg border border-border bg-bg/60 px-2.5 py-1.5 text-[11.5px]">
      <div className="flex items-center gap-1.5">{t.done ? <Wrench className={cx("h-3 w-3", t.ok === false ? "text-warn" : "text-accent")} /> : <Loader2 className="h-3 w-3 animate-spin text-info" />}
        <span className="font-medium">{t.label || t.name}</span>{(t.summary || t.message) && <span className="truncate text-muted">· {t.done ? t.summary : t.message}</span>}</div>
      {t.terminal && <details className="mt-1"><summary className="cursor-pointer font-mono text-[11px] text-muted">$ {t.terminal.command.slice(0, 140)}</summary>
        <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-[#0d1117] p-2 font-mono text-[11px] text-[#c9d1d9]">{t.terminal.stdout}{t.terminal.stderr ? `\n${t.terminal.stderr}` : ""}</pre></details>}
      {changed.length > 0 && <div className="mt-1 flex flex-wrap gap-1">{changed.slice(0, 10).map((p) => <button key={p} className="rounded border border-border px-1.5 font-mono text-[10.5px] hover:bg-elevated" onClick={() => onOpen(p)}>{p}</button>)}</div>}
      {t.done && t.ok === false && t.remediation && <p className="mt-1 text-warn">{t.remediation}</p>}
      {!t.done && t.name === "ws_run" && <p className="mt-0.5 text-[10.5px] text-subtle">{L("Se ejecuta en el Spark, sin internet", "Runs on the Spark, no internet")}</p>}
    </div>
  );
}

function Assistant({ wid, onFiles, onOpen }: { wid: string; onFiles: () => void; onOpen: (p: string) => void }) {
  const L = useL();
  const { push } = useToast();
  const [cid, setCid] = useState<string | null>(null);
  const [model, setModel] = useState<string>("");
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [think, setThink] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const upd = (fn: (m: ChatMsg) => ChatMsg) => setMsgs((x) => (x.length ? [...x.slice(0, -1), fn(x[x.length - 1])] : x));
  const onEvent = useCallback((ev: any) => {
    if (ev.type === "delta") upd((m) => ({ ...m, content: m.content + ev.content }));
    else if (ev.type === "tool_start") upd((m) => ({ ...m, tools: [...(m.tools ?? []), { id: ev.id, name: ev.name, label: ev.label, args: ev.args }] }));
    else if (ev.type === "tool_progress") upd((m) => ({ ...m, tools: (m.tools ?? []).map((t) => (t.id === ev.id ? { ...t, message: ev.message } : t)) }));
    else if (ev.type === "tool_result") { upd((m) => ({ ...m, tools: (m.tools ?? []).map((t) => (t.id === ev.id ? { ...t, ...ev, done: true } : t)) })); if (ev.workspace) onFiles(); }
    else if (ev.type === "done") upd((m) => ({ ...m, pending: false }));
    else if (ev.type === "error") { upd((m) => ({ ...m, pending: false })); push("error", ev.message); }
  }, [onFiles, push]);
  // Follow a reply: the turn runs on the server, so leaving the page never stops it; coming back re-attaches.
  const follow = useCallback(async (run: (signal: AbortSignal) => Promise<void>) => {
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    setBusy(true);
    try { await run(ctl.signal); } catch (e: any) { if (e.name !== "AbortError") push("error", e.message); }
    finally { if (abort.current === ctl) { setBusy(false); upd((m) => ({ ...m, pending: false })); onFiles(); } }
  }, [onFiles, push]);
  const load = useCallback(async (fresh = false) => {
    const r = await api(`/api/v1/workspaces/${wid}/conversation${fresh ? "?new=true" : ""}`, { method: "POST" });
    setCid(r.conversation_id); setModel(r.model_alias ?? "");
    const c = await api(`/api/v1/playground/conversations/${r.conversation_id}`);
    const history: ChatMsg[] = c.messages.map((m: any) => ({ role: m.role, content: m.content, tools: (m.parts ?? []).filter((p: any) => p.type === "tool").map((p: any) => ({ ...p, done: true })) }));
    if (c.active_turn) {
      setMsgs([...history, { role: "assistant", content: "", tools: [], pending: true }]);
      follow((signal) => streamGet(`/api/v1/playground/conversations/${r.conversation_id}/stream`, (ev) => { if (ev.type !== "gone") onEvent(ev); }, signal));
    } else setMsgs(history);
  }, [wid, follow, onEvent]);
  useEffect(() => { load().catch((e) => push("error", e.message)); return () => abort.current?.abort(); }, [load, push]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs]);
  async function send() {
    const content = input.trim();
    if (!content || !cid || busy) return;
    setInput("");
    setMsgs((x) => [...x, { role: "user", content }, { role: "assistant", content: "", tools: [], pending: true }]);
    await follow((signal) => streamPost(`/api/v1/playground/conversations/${cid}/messages`, { content, model_alias: model, max_tokens: 8192, temperature: 0.3, tools: true, thinking: think, web: true, workspace_id: wid }, onEvent, signal));
  }
  async function stop() { if (cid) await api(`/api/v1/playground/conversations/${cid}/cancel`, { method: "POST" }).catch(() => {}); abort.current?.abort(); }
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b border-border px-3 py-2">
        <span className="text-sm font-semibold">{L("Asistente", "Assistant")}</span>{model && <Badge>{model}</Badge>}
        <Button size="sm" variant="ghost" className="ml-auto" disabled={busy} onClick={() => load(true)} title={L("Nueva conversación (los archivos se quedan)", "New conversation (files stay)")}><MessageSquarePlus className="h-3.5 w-3.5" /></Button>
      </div>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3 text-sm">
        {msgs.length === 0 && <div className="space-y-2 text-xs text-muted">
          <p>{L("Pídele trabajo sobre tus archivos; escribe el código, lo ejecuta aquí y corrige los errores. Por ejemplo:", "Ask it to work on your files; it writes the code, runs it here and fixes errors. For example:")}</p>
          {[L("Con datos.csv hazme un Excel con un resumen por mes y un gráfico", "From data.csv make an Excel with a monthly summary and a chart"),
            L("Genera un informe PDF de 2 páginas con las conclusiones de informe.md", "Make a 2-page PDF report from the conclusions in report.md"),
            L("Crea un script que limpie los CSV de la carpeta datos/ y pruébalo", "Write a script that cleans the CSVs in data/ and test it"),
            L("Revisa mi código de análisis, ejecuta los tests y arregla lo que falle", "Review my analysis code, run the tests and fix what fails")].map((s) => (
            <button key={s} className="block w-full rounded-lg border border-border p-2 text-left hover:bg-elevated" onClick={() => setInput(s)}>{s}</button>))}
        </div>}
        {msgs.map((m, i) => m.role === "user"
          ? <div key={i} className="ml-6 whitespace-pre-wrap rounded-xl bg-accent px-3 py-2 text-accent-fg">{m.content}</div>
          : <div key={i}>{m.tools?.map((t, j) => <ToolLine key={t.id ?? j} t={t} onOpen={onOpen} />)}
              {m.content ? <div className="prose-chat"><ReactMarkdown remarkPlugins={[remarkGfm]}>{m.content}</ReactMarkdown></div> : m.pending && !(m.tools?.length) && <Loader2 className="h-4 w-4 animate-spin text-muted" />}</div>)}
        <div ref={end} />
      </div>
      <form className="space-y-1.5 border-t border-border p-2" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <Textarea rows={3} className="resize-none text-sm" value={input} onChange={(e) => setInput(e.target.value)} placeholder={L("¿Qué hacemos en este workspace?", "What shall we do in this workspace?")}
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }} />
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-1.5 text-xs text-muted"><input type="checkbox" checked={think} onChange={(e) => setThink(e.target.checked)} />{L("Pensar (más lento, mejor en problemas difíciles)", "Think (slower, better for hard problems)")}</label>
          {busy ? <Button type="button" size="sm" variant="danger" className="ml-auto" onClick={stop}><Square className="h-3.5 w-3.5" />{L("Parar", "Stop")}</Button>
            : <Button type="submit" size="sm" variant="primary" className="ml-auto" disabled={!input.trim() || !cid}><Send className="h-3.5 w-3.5" />{L("Enviar", "Send")}</Button>}
        </div>
      </form>
    </div>
  );
}

export default function WorkspacePage() {
  const L = useL();
  const { push } = useToast();
  const { data, mutate } = useSWR("/api/v1/workspaces");
  const [wid, setWid] = useState<string | null>(null);
  const ws = data?.workspaces?.find((w: any) => w.id === wid) ?? data?.workspaces?.[0];
  useEffect(() => { if (ws && !wid) setWid(ws.id); }, [ws, wid]);
  const { data: tree, mutate: mutTree } = useSWR(ws ? `/api/v1/workspaces/${ws.id}/tree` : null);
  const [sel, setSel] = useState<string | null>(null);
  // Terminal runs live on the server (Redis): leaving the page or opening another window never loses them.
  const { data: runsData, mutate: mutRuns } = useSWR(ws ? `/api/v1/workspaces/${ws.id}/runs` : null,
    { refreshInterval: (d: any) => (d?.runs?.some((r: any) => r.status === "running") ? 1500 : 0) });
  const runs: Run[] = (runsData?.runs ?? []).map((r: any) => ({ ...r, running: r.status === "running" }));
  const running = runs.some((r) => r.running);
  const wasRunning = useRef<Set<string>>(new Set());
  const [fileVersion, setFileVersion] = useState(0);
  const [dialog, setDialog] = useState<null | { kind: "file" | "dir" | "ws" | "rename"; value: string }>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [pane, setPane] = useState<"files" | "editor" | "chat">("editor");
  const fileInput = useRef<HTMLInputElement>(null);
  const entries: Entry[] = tree?.entries ?? [];
  const selDir = useMemo(() => { if (!sel) return ""; const e = entries.find((x) => x.path === sel); return e?.type === "dir" ? sel : sel.split("/").slice(0, -1).join("/"); }, [sel, entries]);
  useEffect(() => { setSel(null); }, [ws?.id]);
  const refresh = useCallback(() => { mutTree(); mutate(); setFileVersion((v) => v + 1); }, [mutTree, mutate]);
  const open = (p: string) => { setSel(p); setPane("editor"); };
  useEffect(() => {  // a command finished (here or while away): show the files it changed
    const now = new Set(runs.filter((r: any) => r.running).map((r: any) => r.id));
    if ([...wasRunning.current].some((id) => !now.has(id))) refresh();
    wasRunning.current = now;
  }, [runsData]); // eslint-disable-line react-hooks/exhaustive-deps
  async function run(command: string) {
    if (!ws) return;
    try { await api(`/api/v1/workspaces/${ws.id}/run`, { method: "POST", json: { command, background: true } }); await mutRuns(); }
    catch (e: any) { push("error", e.message); }
  }
  async function uploadFiles(fs: FileList | File[]) {
    if (!ws) return;
    for (const f of Array.from(fs)) {
      const fd = new FormData(); fd.append("file", f); fd.append("folder", selDir);
      try { await upload(`/api/v1/workspaces/${ws.id}/upload`, fd); } catch (e: any) { push("error", `${f.name}: ${e.message}`); }
    }
    refresh(); push("ok", L("Subido", "Uploaded"));
  }
  async function submitDialog() {
    if (!dialog || !dialog.value.trim()) return;
    const v = dialog.value.trim();
    try {
      if (dialog.kind === "ws") { const w = await api("/api/v1/workspaces", { method: "POST", json: { name: v } }); await mutate(); setWid(w.id); }
      else if (dialog.kind === "file") { const p = `${selDir ? `${selDir}/` : ""}${v}`; await api(`/api/v1/workspaces/${ws.id}/file`, { method: "PUT", json: { path: p, content: "" } }); refresh(); setSel(p); }
      else if (dialog.kind === "dir") { await api(`/api/v1/workspaces/${ws.id}/mkdir`, { method: "POST", json: { path: `${selDir ? `${selDir}/` : ""}${v}` } }); refresh(); }
      else if (dialog.kind === "rename" && sel) { const dst = [...sel.split("/").slice(0, -1), v].join("/"); await api(`/api/v1/workspaces/${ws.id}/move`, { method: "POST", json: { src: sel, dst } }); refresh(); setSel(dst); }
      setDialog(null);
    } catch (e: any) { push("error", e.message); }
  }
  const selEntry = entries.find((e) => e.path === sel);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <SquareTerminal className="h-5 w-5 text-accent" /><h1 className="text-lg font-semibold">Workspace</h1>
        <Select className="!h-8 !w-auto max-w-[260px]" value={ws?.id ?? ""} onChange={(e) => setWid(e.target.value)}>{data?.workspaces?.map((w: any) => <option key={w.id} value={w.id}>{w.name}</option>)}</Select>
        <Button size="sm" variant="ghost" onClick={() => setDialog({ kind: "ws", value: "" })}><Plus className="h-3.5 w-3.5" />{L("Nuevo", "New")}</Button>
        {ws && <a href={`/api/v1/workspaces/${ws.id}/zip`}><Button size="sm" variant="ghost"><Download className="h-3.5 w-3.5" />ZIP</Button></a>}
        {ws && data.workspaces.length > 1 && <Button size="sm" variant="ghost" onClick={async () => { if (!window.confirm(L(`¿Borrar el workspace «${ws.name}» y todos sus archivos?`, `Delete workspace “${ws.name}” and all its files?`))) return; await api(`/api/v1/workspaces/${ws.id}`, { method: "DELETE" }); setWid(null); mutate(); }}><Trash2 className="h-3.5 w-3.5" /></Button>}
        {ws && <span className="text-xs text-muted">{bytes(ws.size_bytes)} / {data.limits.max_gb} GB · {L("comandos hasta", "commands up to")} {Math.round(data.limits.timeout_s / 60)} min · {L("sin internet", "no internet")}</span>}
        <div className="ml-auto flex gap-1 lg:hidden">{(["files", "editor", "chat"] as const).map((p) => <Button key={p} size="sm" variant={pane === p ? "primary" : "ghost"} onClick={() => setPane(p)}>{p === "files" ? L("Archivos", "Files") : p === "editor" ? L("Editor", "Editor") : L("Asistente", "Assistant")}</Button>)}</div>
      </div>
      <Guide id="workspace" />
      {data && !data.limits.code_enabled && <Notice tone="warn">{L("La ejecución de código está desactivada: puedes editar archivos, pero el terminal y el asistente no podrán ejecutar nada.", "Code execution is disabled: you can edit files, but the terminal and the assistant cannot run anything.")}</Notice>}
      <div className="grid h-[calc(100dvh-12rem)] min-h-[560px] gap-3 lg:h-[calc(100vh-9.5rem)] lg:grid-cols-[250px_minmax(0,1fr)_minmax(320px,0.8fr)]">
        <Card className={cx("min-h-0 flex-col overflow-hidden lg:flex", pane === "files" ? "flex" : "hidden")}
          onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); uploadFiles(e.dataTransfer.files); }}>
          <div className="flex flex-wrap gap-0.5 border-b border-border p-1.5">
            <Button size="icon" variant="ghost" title={L("Subir archivos (o arrástralos aquí)", "Upload files (or drop them here)")} onClick={() => fileInput.current?.click()}><Upload className="h-4 w-4" /></Button>
            <Button size="icon" variant="ghost" title={L("Traer desde Mis archivos", "Bring from My files")} onClick={() => setImportOpen(true)}><HardDriveDownload className="h-4 w-4" /></Button>
            <Button size="icon" variant="ghost" title={L("Nuevo archivo", "New file")} onClick={() => setDialog({ kind: "file", value: "" })}><FilePlus2 className="h-4 w-4" /></Button>
            <Button size="icon" variant="ghost" title={L("Nueva carpeta", "New folder")} onClick={() => setDialog({ kind: "dir", value: "" })}><FolderPlus className="h-4 w-4" /></Button>
            <Button size="icon" variant="ghost" title={L("Actualizar", "Refresh")} onClick={refresh}><RefreshCw className="h-4 w-4" /></Button>
            {sel && <><Button size="icon" variant="ghost" title={L("Renombrar", "Rename")} onClick={() => setDialog({ kind: "rename", value: sel.split("/").pop() ?? "" })}><Pencil className="h-4 w-4" /></Button>
              <Button size="icon" variant="ghost" title={L("Borrar", "Delete")} onClick={async () => { if (!window.confirm(L(`¿Borrar ${sel}?`, `Delete ${sel}?`))) return; await api(`/api/v1/workspaces/${ws.id}/file?path=${encodeURIComponent(sel)}`, { method: "DELETE" }); setSel(null); refresh(); }}><Trash2 className="h-4 w-4" /></Button></>}
            <input ref={fileInput} type="file" multiple className="hidden" onChange={(e) => { if (e.target.files) uploadFiles(e.target.files); e.target.value = ""; }} />
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-1.5">{tree ? <Tree entries={entries} selected={sel} onOpen={(e) => open(e.path)} /> : <Loader2 className="m-3 h-4 w-4 animate-spin" />}</div>
          {selDir && <p className="truncate border-t border-border px-2 py-1 text-[10.5px] text-subtle">{L("Carpeta", "Folder")}: {selDir}/</p>}
        </Card>
        <div className={cx("min-h-0 flex-col gap-3 lg:flex", pane === "editor" ? "flex" : "hidden")}>
          <Card className="min-h-0 flex-[3] overflow-hidden">
            {ws && sel && selEntry?.type === "file" ? <Viewer key={`${sel}`} wid={ws.id} path={sel} version={fileVersion} onRun={run} onSaved={refresh} />
              : <div className="grid h-full place-items-center p-6 text-center text-sm text-muted">{L("Abre un archivo de la izquierda, sube datos o pídele algo al asistente.", "Open a file on the left, upload data or ask the assistant.")}</div>}
          </Card>
          <Card className="min-h-[160px] flex-[2] overflow-hidden"><Terminal runs={runs} busy={running} onRun={run} onOpen={open} onClear={async () => { if (ws) { await api(`/api/v1/workspaces/${ws.id}/runs`, { method: "DELETE" }); mutRuns(); } }} /></Card>
        </div>
        <Card className={cx("min-h-0 overflow-hidden lg:block", pane === "chat" ? "block" : "hidden")}>{ws && <Assistant key={ws.id} wid={ws.id} onFiles={refresh} onOpen={open} />}</Card>
      </div>
      <Modal open={!!dialog} onOpenChange={(o) => !o && setDialog(null)} title={dialog?.kind === "ws" ? L("Nuevo workspace", "New workspace") : dialog?.kind === "dir" ? L("Nueva carpeta", "New folder") : dialog?.kind === "rename" ? L("Renombrar", "Rename") : L("Nuevo archivo", "New file")}
        description={dialog?.kind === "file" || dialog?.kind === "dir" ? `${L("En", "In")} /${selDir}` : undefined}>
        <form onSubmit={(e) => { e.preventDefault(); submitDialog(); }} className="flex gap-2">
          <Input autoFocus value={dialog?.value ?? ""} onChange={(e) => setDialog((d) => (d ? { ...d, value: e.target.value } : d))} placeholder={dialog?.kind === "file" ? "analisis.py" : dialog?.kind === "ws" ? L("Tesis, Proyecto X…", "Thesis, Project X…") : ""} />
          <Button type="submit" variant="primary">{L("Aceptar", "OK")}</Button>
        </form>
      </Modal>
      {importOpen && ws && <ImportDialog wid={ws.id} folder={selDir} onClose={() => setImportOpen(false)} onDone={() => { setImportOpen(false); refresh(); }} />}
    </div>
  );
}

function ImportDialog({ wid, folder, onClose, onDone }: { wid: string; folder: string; onClose: () => void; onDone: () => void }) {
  const L = useL();
  const { push } = useToast();
  const [q, setQ] = useState("");
  const { data } = useSWR(`/api/v1/files?limit=200${q ? `&q=${encodeURIComponent(q)}` : ""}`);
  const [pick, setPick] = useState<Set<string>>(new Set());
  const list: any[] = Array.isArray(data) ? data : data?.files ?? data?.items ?? [];
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={L("Traer desde Mis archivos", "Bring from My files")} description={`${L("Se copian a", "Copied to")} /${folder}`}>
      <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={L("Buscar…", "Search…")} />
      <div className="mt-2 max-h-80 space-y-0.5 overflow-y-auto">{list.map((f) => (
        <label key={f.id} className="flex items-center gap-2 rounded px-2 py-1 text-sm hover:bg-elevated"><input type="checkbox" checked={pick.has(f.id)} onChange={() => setPick((s) => { const n = new Set(s); n.has(f.id) ? n.delete(f.id) : n.add(f.id); return n; })} />
          <span className="truncate">{f.folder ? `${f.folder}/` : ""}{f.filename}</span><span className="ml-auto text-[11px] text-subtle">{bytes(f.size_bytes)}</span></label>))}</div>
      <div className="mt-3 flex justify-end gap-2"><Button variant="ghost" onClick={onClose}>{L("Cancelar", "Cancel")}</Button>
        <Button variant="primary" disabled={!pick.size} onClick={async () => { try { await api(`/api/v1/workspaces/${wid}/import`, { method: "POST", json: { file_ids: [...pick], folder } }); onDone(); } catch (e: any) { push("error", e.message); } }}>{L(`Copiar ${pick.size}`, `Copy ${pick.size}`)}</Button></div>
    </Modal>
  );
}
