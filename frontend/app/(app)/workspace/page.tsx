"use client";
// Workspace: a persistent remote folder per user with files, an editor with previews, a sandboxed terminal and an
// assistant that writes, runs and fixes code in it (workspace tools ws_*). Desktop: resizable panels
// (files | editor + terminal | assistant) remembered in this browser; mobile: one panel at a time.
import {
  FileCode, FilePlus2, FileText, FolderOpen, FolderPlus, HardDriveDownload, Info, Loader2, PanelBottom, PanelLeft, PanelLeftClose, PanelRight,
  PanelRightClose, RefreshCw, Search, Sparkles, SquareTerminal, Upload, X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { Guide } from "@/components/guide";
import { Button, Field, Input, Modal, Notice, Progress, Select, Tip, cx } from "@/components/ui";
import { Assistant } from "@/components/workspace/assistant";
import {
  type Draft, type Entry, FileIcon, type Run, RUNNABLE, Splitter, type Ws, baseName, ext, parentOf, rawUrl, useConfirm, useMedia, usePersisted,
} from "@/components/workspace/common";
import { ManageDialog, WorkspaceSwitcher, WsDialog } from "@/components/workspace/switcher";
import { Terminal } from "@/components/workspace/terminal";
import { FileTree, type TreeAction } from "@/components/workspace/tree";
import { Viewer } from "@/components/workspace/viewer";
import { api, upload } from "@/lib/api";
import { bytes } from "@/lib/format";
import { useL } from "@/lib/i18n";
import { useToast } from "@/lib/providers";

type Layout = { files: number; chat: number; term: number; showFiles: boolean; showChat: boolean; showTerm: boolean };
const LAYOUT: Layout = { files: 260, chat: 380, term: 240, showFiles: true, showChat: true, showTerm: true };
const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));
const LAST_KEY = "sah-ws-last";

export default function WorkspacePage() {
  const L = useL();
  const { push } = useToast();
  const { data, mutate } = useSWR("/api/v1/workspaces");
  const list: Ws[] = data?.workspaces ?? [];
  const [wid, setWid] = useState<string | null>(null);
  const ws = list.find((w) => w.id === wid) ?? list[0];
  const [layout, setLayout] = usePersisted<Layout>("sah-ws-layout", LAYOUT);
  const [wsDialog, setWsDialog] = useState<null | { w?: Ws }>(null);
  const [manage, setManage] = useState(false);
  const dirty = useRef(0);
  const { confirm, confirmElement } = useConfirm();
  // stable callbacks: the assistant re-attaches its stream whenever its callbacks change
  const onSizes = useCallback(() => { mutate(); }, [mutate]);
  const onDirty = useCallback((n: number) => { dirty.current = n; }, []);
  // ?w=<id> (links from "chats working") wins; otherwise the workspace last opened in this browser
  useEffect(() => {
    let last: string | null = null;
    try { last = localStorage.getItem(LAST_KEY); } catch { /* storage unavailable */ }
    const w = new URLSearchParams(window.location.search).get("w") || last;
    if (w) setWid(w);
  }, []);
  function remember(id: string) {
    try { localStorage.setItem(LAST_KEY, id); } catch { /* storage unavailable */ }
    try { const u = new URL(window.location.href); u.searchParams.set("w", id); window.history.replaceState(null, "", u.toString()); } catch { /* ignore */ }
  }
  async function switchTo(id: string) {
    if (id === ws?.id) return;
    if (dirty.current > 0 && !(await confirm({ title: L("Hay cambios sin guardar", "There are unsaved changes"), danger: true,
      text: L(`Tienes ${dirty.current} archivo(s) con cambios sin guardar en este workspace. Si cambias, se perderán.`, `You have ${dirty.current} file(s) with unsaved changes in this workspace. They will be lost if you switch.`) }))) return;
    dirty.current = 0;
    setWid(id);
    remember(id);
  }
  async function saveWs(name: string, description: string) {
    try {
      if (wsDialog?.w) { await api(`/api/v1/workspaces/${wsDialog.w.id}`, { method: "PATCH", json: { name, description } }); await mutate(); push("ok", L("Workspace renombrado", "Workspace renamed")); }
      else { const w = await api("/api/v1/workspaces", { method: "POST", json: { name, description } }); await mutate(); setManage(false); await switchTo(w.id); push("ok", L(`Workspace «${name}» creado`, `Workspace “${name}” created`)); }
      setWsDialog(null);
    } catch (e: any) { push("error", e.message); }
  }
  async function deleteWs(w: Ws) {
    if (!(await confirm({ title: L(`Borrar «${w.name}»`, `Delete “${w.name}”`), danger: true,
      text: L(`Se borrarán todos sus archivos (${bytes(w.size_bytes)}) y su chat. No se puede deshacer; descarga el ZIP antes si lo necesitas.`, `All its files (${bytes(w.size_bytes)}) and its chat will be deleted. This cannot be undone; download the ZIP first if you need it.`) }))) return;
    try {
      await api(`/api/v1/workspaces/${w.id}`, { method: "DELETE" });
      if (w.id === ws?.id) { dirty.current = 0; setWid(null); try { localStorage.removeItem(LAST_KEY); } catch { /* storage unavailable */ } }
      await mutate();
      push("ok", L("Workspace borrado", "Workspace deleted"));
    } catch (e: any) { push("error", e.message); }
  }
  const limits = data?.limits;
  const used = ws?.size_bytes ?? 0;
  const max = (limits?.max_gb ?? 10) * 1e9;
  const toggle = (k: "showFiles" | "showChat" | "showTerm") => setLayout((l) => ({ ...l, [k]: !l[k] }));
  const PanelBtn = ({ k, icon, label }: { k: "showFiles" | "showChat" | "showTerm"; icon: React.ReactNode; label: string }) => (
    <button type="button" onClick={() => toggle(k)} aria-pressed={layout[k]} title={label}
      className={cx("grid h-8 w-8 place-items-center rounded-lg transition", layout[k] ? "bg-accent/12 text-accent" : "text-muted hover:bg-elevated hover:text-fg")}>{icon}</button>
  );
  return (
    <div className="flex h-[calc(100dvh-10.5rem)] min-h-[480px] flex-col gap-3 lg:h-[calc(100dvh-6.5rem)]">
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <WorkspaceSwitcher list={list} current={ws} onSwitch={switchTo} onNew={() => setWsDialog({})} onRename={(w) => setWsDialog({ w })} onManage={() => setManage(true)} onDelete={deleteWs} />
        {ws && limits && (
          <div className="hidden items-center gap-2 text-[11px] text-muted sm:flex" title={L("Espacio usado en este workspace", "Space used in this workspace")}>
            <Progress value={(used / max) * 100} tone={used / max > 0.9 ? "danger" : used / max > 0.75 ? "warn" : "accent"} className="w-20" />
            <span className="tabular-nums">{bytes(used)} / {limits.max_gb} GB</span>
            <Tip text={L(`Cada comando puede durar hasta ${Math.round(limits.timeout_s / 60)} min. ${limits.packages ? "Sin internet salvo para instalar paquetes con pip/npm." : "Sin internet."} Todo se ejecuta aislado en el Spark.`, `Each command can run for up to ${Math.round(limits.timeout_s / 60)} min. ${limits.packages ? "No internet except to install packages with pip/npm." : "No internet."} Everything runs isolated on the Spark.`)}>
              <button type="button" aria-label={L("Límites", "Limits")} className="text-subtle hover:text-muted"><Info className="h-3.5 w-3.5" /></button>
            </Tip>
          </div>
        )}
        <div className="ml-auto hidden items-center gap-0.5 rounded-xl border border-border bg-surface p-0.5 lg:flex" role="group" aria-label={L("Paneles", "Panels")}>
          <PanelBtn k="showFiles" icon={<PanelLeft className="h-4 w-4" />} label={L("Mostrar/ocultar archivos", "Show/hide files")} />
          <PanelBtn k="showTerm" icon={<PanelBottom className="h-4 w-4" />} label={L("Mostrar/ocultar terminal (Ctrl+`)", "Show/hide terminal (Ctrl+`)")} />
          <PanelBtn k="showChat" icon={<PanelRight className="h-4 w-4" />} label={L("Mostrar/ocultar asistente", "Show/hide assistant")} />
        </div>
      </div>
      <Guide id="workspace" className="!mb-0 shrink-0" />
      {limits && !limits.code_enabled && <Notice tone="warn">{L("La ejecución de código está desactivada: puedes editar archivos, pero el terminal y el asistente no podrán ejecutar nada. Un administrador puede activarla en Ajustes.", "Code execution is disabled: you can edit files, but the terminal and the assistant cannot run anything. An administrator can enable it in Settings.")}</Notice>}
      {!ws ? <div className="grid flex-1 place-items-center"><Loader2 className="h-5 w-5 animate-spin text-muted" /></div>
        : <WorkspaceIDE key={ws.id} ws={ws} limits={limits} layout={layout} setLayout={setLayout} onDirty={onDirty} onSizes={onSizes} />}
      {wsDialog && <WsDialog w={wsDialog.w} onClose={() => setWsDialog(null)} onSubmit={saveWs} />}
      {manage && <ManageDialog list={list} current={ws} maxGb={limits?.max_gb} onClose={() => setManage(false)} onOpen={(id) => { setManage(false); switchTo(id); }}
        onNew={() => setWsDialog({})} onRename={(w) => setWsDialog({ w })} onDelete={deleteWs} />}
      {confirmElement}
    </div>
  );
}

type Pane = "files" | "editor" | "terminal" | "chat";
type NameDialog = { kind: "file" | "dir" | "rename" | "move"; entry?: Entry; folder: string; value: string };

function WorkspaceIDE({ ws, limits, layout, setLayout, onDirty, onSizes }: {
  ws: Ws; limits: any; layout: Layout; setLayout: (v: Layout | ((p: Layout) => Layout)) => void; onDirty: (n: number) => void; onSizes: () => void;
}) {
  const L = useL();
  const { push } = useToast();
  const desktop = useMedia("(min-width: 1024px)");
  const base = `/api/v1/workspaces/${ws.id}`;
  const { data: tree, mutate: mutTree } = useSWR(`${base}/tree`);
  const entries: Entry[] = useMemo(() => tree?.entries ?? [], [tree]);
  const byPath = useMemo(() => new Map(entries.map((e) => [e.path, e])), [entries]);
  // Terminal runs live on the server (Redis): leaving the page or opening another window never loses them.
  const { data: runsData, mutate: mutRuns } = useSWR(`${base}/runs`, { refreshInterval: (d: any) => (d?.runs?.some((r: any) => r.status === "running") ? 1500 : 0) });
  const runs: Run[] = useMemo(() => (runsData?.runs ?? []).map((r: any) => ({ ...r, running: r.status === "running" })), [runsData]);
  const running = runs.filter((r) => r.running).length;
  const [open, setOpen] = usePersisted<{ tabs: string[]; active: string | null }>(`sah-ws-tabs:${ws.id}`, { tabs: [], active: null });
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [sel, setSel] = useState<string | null>(null);
  const [closed, setClosed] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState("");
  const [fileVersion, setFileVersion] = useState(0);
  const [pane, setPane] = useState<Pane>("editor");
  const [dialog, setDialog] = useState<NameDialog | null>(null);
  const [importTo, setImportTo] = useState<string | null>(null);
  const [uploading, setUploading] = useState<{ done: number; total: number } | null>(null);
  const [dropEditor, setDropEditor] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadTo = useRef("");
  const saveRef = useRef<(() => Promise<void>) | null>(null);
  const center = useRef<HTMLDivElement>(null);
  const wasRunning = useRef<Set<string>>(new Set());
  const pruned = useRef(false);
  const { confirm, confirmElement } = useConfirm();
  const dirtyCount = Object.keys(drafts).length;
  const active = open.active && open.tabs.includes(open.active) ? open.active : null;
  const selEntry = sel ? byPath.get(sel) : undefined;
  const targetDir = sel ? (selEntry?.type === "dir" ? sel : parentOf(sel)) : "";

  useEffect(() => { onDirty(dirtyCount); }, [dirtyCount, onDirty]);
  useEffect(() => {  // folders hidden by the server (node_modules, .venv…) start closed
    setClosed((c) => { const n = new Set(c); entries.filter((e) => e.collapsed).forEach((e) => n.add(e.path)); return n; });
  }, [entries]);
  useEffect(() => {  // once per visit: forget tabs of files that are gone; a new workspace opens its LEEME.md
    if (!tree || pruned.current) return;
    pruned.current = true;
    setOpen((o) => {
      const tabs = o.tabs.filter((p) => byPath.get(p)?.type === "file");
      if (!tabs.length && entries.length <= 3 && byPath.has("LEEME.md")) return { tabs: ["LEEME.md"], active: "LEEME.md" };
      return { tabs, active: o.active && tabs.includes(o.active) ? o.active : tabs[0] ?? null };
    });
  }, [tree, byPath, entries.length, setOpen]);

  const refresh = useCallback(() => { mutTree(); onSizes(); setFileVersion((v) => v + 1); }, [mutTree, onSizes]);
  useEffect(() => {  // a command finished (here or while away): show the files it changed
    const now = new Set(runs.filter((r) => r.running).map((r) => r.id));
    if ([...wasRunning.current].some((id) => !now.has(id))) refresh();
    wasRunning.current = now;
  }, [runsData]); // eslint-disable-line react-hooks/exhaustive-deps

  const expandTo = (p: string, self = false) => setClosed((c) => {
    const n = new Set(c); const parts = p.split("/");
    for (let i = 1; i < parts.length + (self ? 1 : 0); i++) n.delete(parts.slice(0, i).join("/"));
    return n;
  });
  const openFile = useCallback((p: string) => {
    setOpen((o) => ({ tabs: o.tabs.includes(p) ? o.tabs : [...o.tabs, p], active: p }));
    setSel(p); expandTo(p); setPane("editor");
  }, [setOpen]);
  const reveal = (dir: string) => {
    setSel(dir || null); if (dir) expandTo(dir, true);
    if (desktop) setLayout((l) => ({ ...l, showFiles: true })); else setPane("files");
  };
  async function closeTab(p: string) {
    if (drafts[p] && !(await confirm({ title: L("¿Cerrar sin guardar?", "Close without saving?"), danger: true, text: L(`${baseName(p)} tiene cambios sin guardar que se perderán.`, `${baseName(p)} has unsaved changes that will be lost.`) }))) return;
    setDrafts((d) => { const n = { ...d }; delete n[p]; return n; });
    setOpen((o) => { const i = o.tabs.indexOf(p); const tabs = o.tabs.filter((x) => x !== p); return { tabs, active: o.active === p ? tabs[Math.min(i, tabs.length - 1)] ?? null : o.active }; });
  }
  const onDraft = useCallback((p: string, d: Draft | null) => setDrafts((x) => { const n = { ...x }; if (d) n[p] = d; else delete n[p]; return n; }), []);
  const remap = (src: string, dst: string) => {
    const m = (p: string) => (p === src ? dst : p.startsWith(`${src}/`) ? dst + p.slice(src.length) : p);
    setOpen((o) => ({ tabs: o.tabs.map(m), active: o.active ? m(o.active) : null }));
    setDrafts((d) => Object.fromEntries(Object.entries(d).map(([k, v]) => [m(k), v])));
    setSel((s) => (s ? m(s) : s));
  };
  const forget = (p: string) => {
    const gone = (x: string) => x === p || x.startsWith(`${p}/`);
    setOpen((o) => { const tabs = o.tabs.filter((x) => !gone(x)); return { tabs, active: o.active && !gone(o.active) ? o.active : tabs[tabs.length - 1] ?? null }; });
    setDrafts((d) => Object.fromEntries(Object.entries(d).filter(([k]) => !gone(k))));
    setSel((s) => (s && gone(s) ? null : s));
  };

  // unsaved edits: warn before leaving the page; Ctrl+S saves the open file; Ctrl+` shows or hides the terminal
  useEffect(() => {
    if (!dirtyCount) return;
    const h = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = ""; };
    window.addEventListener("beforeunload", h);
    return () => window.removeEventListener("beforeunload", h);
  }, [dirtyCount]);
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey)) return;
      if (e.key.toLowerCase() === "s" && active) { e.preventDefault(); saveRef.current?.(); }
      if (e.key === "`") { e.preventDefault(); if (desktop) setLayout((l) => ({ ...l, showTerm: !l.showTerm })); else setPane((p) => (p === "terminal" ? "editor" : "terminal")); }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [active, desktop, setLayout]);

  async function run(command: string): Promise<boolean> {
    try {
      await api(`${base}/run`, { method: "POST", json: { command, background: true } });
      await mutRuns();
      if (desktop) setLayout((l) => (l.showTerm ? l : { ...l, showTerm: true })); else setPane("terminal");
      return true;
    } catch (e: any) { push("error", e.message); return false; }
  }
  async function stopRun(id: string) {
    try { await api(`${base}/runs/${id}/stop`, { method: "POST" }); await mutRuns(); push("info", L("Parando el comando…", "Stopping the command…")); }
    catch (e: any) { push("error", e.message); }
  }
  async function uploadFiles(folder: string, fs: FileList | File[]) {
    const files = Array.from(fs);
    if (!files.length) return;
    let ok = 0;
    setUploading({ done: 0, total: files.length });
    for (const f of files) {
      const fd = new FormData(); fd.append("file", f); fd.append("folder", folder);
      try { await upload(`${base}/upload`, fd); ok++; } catch (e: any) { push("error", `${f.name}: ${e.message}`); }
      setUploading((u) => (u ? { ...u, done: u.done + 1 } : u));
    }
    setUploading(null);
    if (folder) expandTo(folder, true);
    refresh();
    if (ok) push("ok", L(`${ok} archivo(s) subido(s) a /${folder}`, `${ok} file(s) uploaded to /${folder}`));
  }
  async function move(src: string, folder: string) {
    const dst = folder ? `${folder}/${baseName(src)}` : baseName(src);
    if (dst === src) return;
    try { await api(`${base}/move`, { method: "POST", json: { src, dst } }); remap(src, dst); if (folder) expandTo(folder, true); refresh(); push("ok", L(`Movido a /${folder}`, `Moved to /${folder}`)); }
    catch (e: any) { push("error", e.message); }
  }
  async function remove(e: Entry) {
    const isDir = e.type === "dir";
    if (!(await confirm({ title: L(`Borrar ${baseName(e.path)}`, `Delete ${baseName(e.path)}`), danger: true,
      text: isDir ? L(`Se borrará la carpeta ${e.path} con todo su contenido. No se puede deshacer.`, `The folder ${e.path} and everything in it will be deleted. This cannot be undone.`)
        : L(`Se borrará ${e.path}. No se puede deshacer.`, `${e.path} will be deleted. This cannot be undone.`) }))) return;
    try { await api(`${base}/file?path=${encodeURIComponent(e.path)}`, { method: "DELETE" }); forget(e.path); refresh(); }
    catch (er: any) { push("error", er.message); }
  }
  function action(a: TreeAction, e: Entry | null) {
    const folder = e ? (e.type === "dir" ? e.path : parentOf(e.path)) : "";
    if (a === "open" && e) openFile(e.path);
    else if (a === "run" && e) run(RUNNABLE[ext(e.path)](e.path));
    else if (a === "newtab" && e) window.open(rawUrl(ws.id, e.path), "_blank", "noopener");
    else if (a === "download") window.location.href = e?.type === "file" ? rawUrl(ws.id, e.path, true) : `${base}/zip${e ? `?path=${encodeURIComponent(e.path)}` : ""}`;
    else if (a === "export" && e) api(`${base}/export`, { method: "POST", json: { path: e.path } }).then((r) => push("ok", L(`Copiado a Mis archivos → ${r.file.folder}`, `Copied to My files → ${r.file.folder}`)), (er) => push("error", er.message));
    else if (a === "copy" && e) navigator.clipboard?.writeText(e.path).then(() => push("ok", L("Ruta copiada", "Path copied")), () => {});
    else if (a === "rename" && e) setDialog({ kind: "rename", entry: e, folder: parentOf(e.path), value: baseName(e.path) });
    else if (a === "move" && e) setDialog({ kind: "move", entry: e, folder: parentOf(e.path), value: parentOf(e.path) });
    else if (a === "delete" && e) remove(e);
    else if (a === "newfile") setDialog({ kind: "file", folder, value: "" });
    else if (a === "newdir") setDialog({ kind: "dir", folder, value: "" });
    else if (a === "upload") { uploadTo.current = folder; fileInput.current?.click(); }
    else if (a === "import") setImportTo(folder);
  }
  async function submitDialog() {
    if (!dialog) return;
    const v = dialog.value.trim().replace(/^\/+|\/+$/g, "");
    const inDir = (n: string) => (dialog.folder ? `${dialog.folder}/${n}` : n);
    try {
      if (dialog.kind === "file") { if (!v) return; const p = inDir(v); await api(`${base}/file`, { method: "PUT", json: { path: p, content: "" } }); refresh(); openFile(p); }
      else if (dialog.kind === "dir") { if (!v) return; const p = inDir(v); await api(`${base}/mkdir`, { method: "POST", json: { path: p } }); expandTo(p, true); setSel(p); refresh(); }
      else if (dialog.kind === "rename" && dialog.entry) {
        if (!v || v === baseName(dialog.entry.path)) { setDialog(null); return; }
        const dst = inDir(v); await api(`${base}/move`, { method: "POST", json: { src: dialog.entry.path, dst } }); remap(dialog.entry.path, dst); refresh();
      } else if (dialog.kind === "move" && dialog.entry) { setDialog(null); await move(dialog.entry.path, v); return; }
      setDialog(null);
    } catch (e: any) { push("error", e.message); }
  }

  const termActions = (
    <button type="button" className="rounded p-1 text-[#8b949e] max-lg:hidden hover:bg-[#30363d] hover:text-[#e6edf3]" title={L("Ocultar el terminal (Ctrl+`)", "Hide the terminal (Ctrl+`)")} onClick={() => setLayout((l) => ({ ...l, showTerm: false }))}><X className="h-3.5 w-3.5" /></button>
  );
  const lastRun = runs[runs.length - 1];
  const sameName = (p: string) => open.tabs.some((t) => t !== p && baseName(t) === baseName(p));
  const ib = "!h-7 !w-7";

  return (
    <div className="flex min-h-[360px] flex-1 flex-col overflow-hidden rounded-2xl border border-border bg-surface shadow-card lg:flex-row">
      {/* mobile: one panel at a time */}
      <div className="flex shrink-0 border-b border-border lg:hidden" role="tablist">
        {([["files", L("Archivos", "Files"), <FolderOpen key="i" className="h-4 w-4" />, 0], ["editor", L("Editor", "Editor"), <FileCode key="i" className="h-4 w-4" />, dirtyCount],
          ["terminal", L("Terminal", "Terminal"), <SquareTerminal key="i" className="h-4 w-4" />, running], ["chat", L("Asistente", "Assistant"), <Sparkles key="i" className="h-4 w-4" />, 0]] as const).map(([p, label, icon, n]) => (
          <button key={p} type="button" role="tab" aria-selected={pane === p} onClick={() => setPane(p)}
            className={cx("relative flex flex-1 flex-col items-center gap-0.5 py-1.5 text-[11px]", pane === p ? "text-accent" : "text-muted")}>
            {icon}{label}{n > 0 && <span className="absolute right-[18%] top-1 grid h-4 min-w-4 place-items-center rounded-full bg-accent px-1 text-[9px] font-bold text-accent-fg">{n}</span>}
            {pane === p && <span className="absolute inset-x-4 bottom-0 h-0.5 rounded-full bg-accent" />}
          </button>))}
      </div>

      {/* files */}
      <aside className={cx("flex min-h-0 min-w-0 flex-col max-lg:flex-1 lg:w-[var(--w)] lg:max-w-[35%] lg:shrink-0", pane !== "files" && "max-lg:hidden", !layout.showFiles && "lg:hidden")}
        style={{ "--w": `${layout.files}px` } as React.CSSProperties} aria-label={L("Archivos", "Files")}>
        <div className="flex h-10 shrink-0 items-center gap-0.5 border-b border-border pl-3 pr-1">
          <span className="mr-auto text-xs font-semibold">{L("Archivos", "Files")}</span>
          <Button size="icon" variant="ghost" className={ib} title={L(`Nuevo archivo en /${targetDir}`, `New file in /${targetDir}`)} onClick={() => action("newfile", targetDir ? byPath.get(targetDir) ?? null : null)}><FilePlus2 className="h-4 w-4" /></Button>
          <Button size="icon" variant="ghost" className={ib} title={L(`Nueva carpeta en /${targetDir}`, `New folder in /${targetDir}`)} onClick={() => action("newdir", targetDir ? byPath.get(targetDir) ?? null : null)}><FolderPlus className="h-4 w-4" /></Button>
          <Button size="icon" variant="ghost" className={ib} title={L(`Subir archivos a /${targetDir} (o arrástralos a una carpeta)`, `Upload files to /${targetDir} (or drop them on a folder)`)} onClick={() => action("upload", targetDir ? byPath.get(targetDir) ?? null : null)}><Upload className="h-4 w-4" /></Button>
          <Button size="icon" variant="ghost" className={ib} title={L("Traer desde Mis archivos", "Bring from My files")} onClick={() => setImportTo(targetDir)}><HardDriveDownload className="h-4 w-4" /></Button>
          <Button size="icon" variant="ghost" className={ib} title={L("Actualizar", "Refresh")} onClick={refresh}><RefreshCw className="h-3.5 w-3.5" /></Button>
          <Button size="icon" variant="ghost" className={cx(ib, "max-lg:hidden")} title={L("Ocultar archivos", "Hide files")} onClick={() => setLayout((l) => ({ ...l, showFiles: false }))}><PanelLeftClose className="h-4 w-4" /></Button>
        </div>
        <div className="shrink-0 px-2 pt-2">
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
            <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={L("Buscar archivo…", "Find a file…")} aria-label={L("Buscar archivo", "Find a file")}
              onKeyDown={(e) => { if (e.key === "Escape") setFilter(""); }}
              className="h-7 w-full rounded-lg border border-border bg-bg pl-7 pr-6 text-xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25" />
            {filter && <button type="button" className="absolute right-1.5 top-1/2 -translate-y-1/2 text-subtle hover:text-fg" onClick={() => setFilter("")} aria-label={L("Borrar búsqueda", "Clear search")}><X className="h-3.5 w-3.5" /></button>}
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-1.5">
          {!tree ? <Loader2 className="m-3 h-4 w-4 animate-spin text-muted" />
            : <>
              <FileTree entries={entries} selected={sel} active={active} closed={closed} filter={filter}
                onToggle={(p) => setClosed((c) => { const n = new Set(c); if (n.has(p)) n.delete(p); else n.add(p); return n; })}
                onSelect={(e) => setSel(e.path)} onAction={action} onDropFiles={uploadFiles} onMove={move} />
              {entries.length === 0 && <p className="px-3 text-center text-xs text-muted">{L("Carpeta vacía. Arrastra archivos aquí, usa ⬆ o pídele al asistente que cree algo.", "Empty folder. Drop files here, use ⬆ or ask the assistant to create something.")}</p>}
            </>}
        </div>
        <div className="shrink-0 border-t border-border px-3 py-1.5 text-[10.5px] text-subtle">
          {uploading ? <span className="flex items-center gap-1.5 text-info"><Loader2 className="h-3 w-3 animate-spin" />{L(`Subiendo ${uploading.done + 1} de ${uploading.total}…`, `Uploading ${uploading.done + 1} of ${uploading.total}…`)}</span>
            : <span className="block truncate" title={L("Lo nuevo (archivos, carpetas, subidas) va a esta carpeta. Selecciona otra carpeta para cambiarla.", "New items (files, folders, uploads) go to this folder. Select another folder to change it.")}>
                {L("Destino", "Target")}: /{targetDir} · {L("clic derecho para más acciones", "right click for more actions")}</span>}
        </div>
        <input ref={fileInput} type="file" multiple className="hidden" onChange={(e) => { if (e.target.files) uploadFiles(uploadTo.current, e.target.files); e.target.value = ""; }} />
      </aside>
      {layout.showFiles && <div className="hidden lg:contents"><Splitter dir="x" label={L("Cambiar el ancho de los archivos", "Resize the files panel")} onReset={() => setLayout((l) => ({ ...l, files: LAYOUT.files }))}
        onDelta={(d) => setLayout((l) => ({ ...l, files: clamp(l.files + d, 180, 520) }))} /></div>}

      {/* editor + terminal */}
      <div ref={center} className={cx("flex min-h-0 min-w-0 flex-1 flex-col", pane !== "editor" && pane !== "terminal" && "max-lg:hidden")}>
        <section className={cx("flex min-h-0 flex-1 flex-col", pane !== "editor" && "max-lg:hidden")} aria-label={L("Editor", "Editor")}>
          {open.tabs.length > 0 && (
            <div className="flex h-10 shrink-0 items-end gap-px overflow-x-auto border-b border-border bg-elevated/40 px-1 pt-1" role="tablist" aria-label={L("Archivos abiertos", "Open files")}>
              {open.tabs.map((p) => (
                <div key={p} role="tab" aria-selected={p === active} title={p}
                  onAuxClick={(e) => { if (e.button === 1) { e.preventDefault(); closeTab(p); } }}
                  className={cx("group flex h-full max-w-[220px] shrink-0 items-center gap-1.5 rounded-t-lg border border-b-0 pl-2.5 pr-1 text-xs transition",
                    p === active ? "border-border bg-surface text-fg" : "border-transparent text-muted hover:bg-elevated hover:text-fg")}>
                  <button type="button" className="flex min-w-0 items-center gap-1.5" onClick={() => { setOpen((o) => ({ ...o, active: p })); setSel(p); expandTo(p); }}>
                    <FileIcon path={p} type="file" />
                    <span className="truncate">{baseName(p)}</span>
                    {sameName(p) && <span className="truncate text-[10.5px] text-subtle">{parentOf(p) || "/"}</span>}
                  </button>
                  <button type="button" onClick={() => closeTab(p)} aria-label={L(`Cerrar ${baseName(p)}`, `Close ${baseName(p)}`)}
                    className="grid h-5 w-5 shrink-0 place-items-center rounded text-subtle hover:bg-border/60 hover:text-fg">
                    {drafts[p] ? <><span className="h-2 w-2 rounded-full bg-accent group-hover:hidden" title={L("Sin guardar", "Unsaved")} /><X className="hidden h-3 w-3 group-hover:block" /></> : <X className={cx("h-3 w-3", p !== active && "opacity-0 group-hover:opacity-100")} />}
                  </button>
                </div>))}
            </div>
          )}
          <div className="min-h-0 flex-1">
            {active ? <Viewer key={active} wid={ws.id} wsName={ws.name} path={active} version={fileVersion} draft={drafts[active]} onDraft={onDraft} saveRef={saveRef}
                onSaved={(p) => { onDraft(p, null); refresh(); }} onRun={run} onReveal={reveal} onClose={() => closeTab(active)} />
              : <div className={cx("grid h-full place-items-center p-6 transition", dropEditor && "bg-accent/[0.06] ring-2 ring-inset ring-accent/40")}
                  onDragOver={(e) => { if (e.dataTransfer.types.includes("Files")) { e.preventDefault(); setDropEditor(true); } }} onDragLeave={() => setDropEditor(false)}
                  onDrop={(e) => { e.preventDefault(); setDropEditor(false); uploadFiles(targetDir, e.dataTransfer.files); }}>
                  <div className="max-w-md space-y-4 text-center">
                    <div className="mx-auto w-fit rounded-full bg-elevated p-3 text-subtle"><FileText className="h-5 w-5" /></div>
                    <p className="text-sm font-medium">{L("Ningún archivo abierto", "No file open")}</p>
                    <ol className="space-y-1.5 text-left text-xs text-muted">
                      <li><span className="mr-1.5 font-semibold text-accent">1.</span>{L("Abre un archivo en la columna Archivos (clic derecho o ⋯ para renombrar, mover, descargar…). Puedes tener varios abiertos en pestañas.", "Open a file from the Files column (right click or ⋯ to rename, move, download…). You can keep several open in tabs.")}</li>
                      <li><span className="mr-1.5 font-semibold text-accent">2.</span>{L(`Arrastra aquí tus datos para subirlos a /${targetDir}, o suéltalos sobre una carpeta concreta.`, `Drop your data here to upload it to /${targetDir}, or drop it on a specific folder.`)}</li>
                      <li><span className="mr-1.5 font-semibold text-accent">3.</span>{L("Ejecuta lo que quieras en el terminal de abajo, o pídele al asistente que escriba y pruebe el código por ti.", "Run anything in the terminal below, or ask the assistant to write and test the code for you.")}</li>
                    </ol>
                    <div className="flex flex-wrap justify-center gap-2">
                      <Button size="sm" variant="outline" onClick={() => action("newfile", targetDir ? byPath.get(targetDir) ?? null : null)}><FilePlus2 className="h-3.5 w-3.5" />{L("Nuevo archivo", "New file")}</Button>
                      <Button size="sm" variant="outline" onClick={() => action("upload", targetDir ? byPath.get(targetDir) ?? null : null)}><Upload className="h-3.5 w-3.5" />{L("Subir archivos", "Upload files")}</Button>
                      {byPath.has("LEEME.md") && <Button size="sm" variant="ghost" onClick={() => openFile("LEEME.md")}>LEEME.md</Button>}
                    </div>
                  </div>
                </div>}
          </div>
        </section>
        {layout.showTerm && <div className="hidden lg:contents"><Splitter dir="y" label={L("Cambiar la altura del terminal", "Resize the terminal")} onReset={() => setLayout((l) => ({ ...l, term: LAYOUT.term }))}
          onDelta={(d) => setLayout((l) => ({ ...l, term: clamp(l.term - d, 110, Math.max(160, (center.current?.clientHeight ?? 600) - 140)) }))} /></div>}
        <div className={cx("min-h-0 max-lg:flex-1 lg:h-[var(--h)] lg:max-h-[75%] lg:shrink-0", pane !== "terminal" && "max-lg:hidden", !layout.showTerm && "lg:hidden")} style={{ "--h": `${layout.term}px` } as React.CSSProperties}>
          <Terminal runs={runs} onRun={run} onStop={stopRun} onOpen={openFile} codeEnabled={!!limits?.code_enabled} timeoutMin={limits ? Math.round(limits.timeout_s / 60) : undefined} packages={limits?.packages}
            onClear={async () => { try { await api(`${base}/runs`, { method: "DELETE" }); await mutRuns(); } catch (e: any) { push("error", e.message); } }} actions={termActions} />
        </div>
        {!layout.showTerm && (
          <button type="button" onClick={() => setLayout((l) => ({ ...l, showTerm: true }))} className="hidden h-8 shrink-0 items-center gap-2 border-t border-border px-3 text-xs text-muted transition hover:bg-elevated hover:text-fg lg:flex">
            <SquareTerminal className="h-3.5 w-3.5" />{L("Terminal", "Terminal")}
            {running > 0 ? <span className="flex items-center gap-1 text-info"><Loader2 className="h-3 w-3 animate-spin" />{L(`${running} en marcha`, `${running} running`)}</span>
              : lastRun && <span className="truncate font-mono text-[11px] text-subtle">$ {lastRun.command.split("\n")[0]} · {lastRun.timed_out ? L("tiempo agotado", "timed out") : `exit ${lastRun.exit_code}`}</span>}
            <span className="ml-auto text-[10.5px] text-subtle">Ctrl+`</span>
          </button>
        )}
      </div>

      {/* assistant */}
      {layout.showChat && <div className="hidden lg:contents"><Splitter dir="x" label={L("Cambiar el ancho del asistente", "Resize the assistant")} onReset={() => setLayout((l) => ({ ...l, chat: LAYOUT.chat }))}
        onDelta={(d) => setLayout((l) => ({ ...l, chat: clamp(l.chat - d, 300, 760) }))} /></div>}
      <aside className={cx("flex min-h-0 min-w-0 flex-col max-lg:flex-1 lg:w-[var(--w)] lg:max-w-[45%] lg:shrink-0", pane !== "chat" && "max-lg:hidden", !layout.showChat && "lg:hidden")}
        style={{ "--w": `${layout.chat}px` } as React.CSSProperties} aria-label={L("Asistente", "Assistant")}>
        <Assistant wid={ws.id} onFiles={refresh} onOpen={openFile}
          actions={<Button size="icon" variant="ghost" className={cx(ib, "max-lg:hidden")} title={L("Ocultar el asistente", "Hide the assistant")} onClick={() => setLayout((l) => ({ ...l, showChat: false }))}><PanelRightClose className="h-4 w-4" /></Button>} />
      </aside>

      {dialog && <NameDialogView dialog={dialog} entries={entries} onChange={(value) => setDialog((d) => (d ? { ...d, value } : d))} onClose={() => setDialog(null)} onSubmit={submitDialog} />}
      {importTo !== null && <ImportDialog wid={ws.id} folder={importTo} onClose={() => setImportTo(null)} onDone={() => { setImportTo(null); refresh(); }} />}
      {confirmElement}
    </div>
  );
}

function NameDialogView({ dialog, entries, onChange, onClose, onSubmit }: { dialog: NameDialog; entries: Entry[]; onChange: (v: string) => void; onClose: () => void; onSubmit: () => void }) {
  const L = useL();
  const where = `/${dialog.folder}`;
  const title = { file: L("Nuevo archivo", "New file"), dir: L("Nueva carpeta", "New folder"), rename: L("Renombrar", "Rename"), move: L("Mover a otra carpeta", "Move to another folder") }[dialog.kind];
  const src = dialog.entry?.path ?? "";
  const folders = entries.filter((e) => e.type === "dir" && e.path !== src && !e.path.startsWith(`${src}/`)).map((e) => e.path);
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={title}
      description={dialog.kind === "rename" || dialog.kind === "move" ? src : `${L("En", "In")} ${where}`}>
      <form onSubmit={(e) => { e.preventDefault(); onSubmit(); }} className="space-y-3">
        {dialog.kind === "move" ? (
          <Field label={L("Carpeta de destino", "Target folder")} hint={L("También puedes arrastrar el archivo sobre una carpeta en la lista.", "You can also drag the file onto a folder in the list.")}>
            <Select autoFocus value={dialog.value} onChange={(e) => onChange(e.target.value)}>
              <option value="">/ ({L("raíz", "root")})</option>
              {folders.map((f) => <option key={f} value={f}>/{f}</option>)}
            </Select>
          </Field>
        ) : (
          <Field label={L("Nombre", "Name")} hint={dialog.kind === "file" ? L("Con extensión (analisis.py, notas.md…). Puedes escribir subcarpeta/nombre para crearla a la vez.", "With extension (analysis.py, notes.md…). You can type subfolder/name to create it at the same time.") : undefined}>
            <Input autoFocus value={dialog.value} onChange={(e) => onChange(e.target.value)} placeholder={dialog.kind === "file" ? "analisis.py" : dialog.kind === "dir" ? "datos" : ""}
              onFocus={(e) => { if (dialog.kind === "rename") { const i = e.target.value.lastIndexOf("."); e.target.setSelectionRange(0, i > 0 ? i : e.target.value.length); } }} />
          </Field>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>{L("Cancelar", "Cancel")}</Button>
          <Button type="submit" variant="primary" disabled={dialog.kind !== "move" && !dialog.value.trim()}>{dialog.kind === "move" ? L("Mover", "Move") : dialog.kind === "rename" ? L("Renombrar", "Rename") : L("Crear", "Create")}</Button>
        </div>
      </form>
    </Modal>
  );
}

function ImportDialog({ wid, folder, onClose, onDone }: { wid: string; folder: string; onClose: () => void; onDone: () => void }) {
  const L = useL();
  const { push } = useToast();
  const [q, setQ] = useState("");
  const { data } = useSWR(`/api/v1/files?limit=200${q ? `&q=${encodeURIComponent(q)}` : ""}`);
  const [pick, setPick] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const list: any[] = Array.isArray(data) ? data : data?.files ?? data?.items ?? [];
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={L("Traer desde Mis archivos", "Bring from My files")} description={`${L("Se copian a", "Copied to")} /${folder}`}>
      <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={L("Buscar…", "Search…")} />
      <div className="mt-2 max-h-80 space-y-0.5 overflow-y-auto">
        {data && list.length === 0 && <p className="px-2 py-4 text-center text-xs text-muted">{L("No hay archivos en Mis archivos que coincidan.", "No files in My files match.")}</p>}
        {list.map((f) => (
          <label key={f.id} className="flex items-center gap-2 rounded px-2 py-1 text-sm hover:bg-elevated"><input type="checkbox" checked={pick.has(f.id)} onChange={() => setPick((s) => { const n = new Set(s); if (n.has(f.id)) n.delete(f.id); else n.add(f.id); return n; })} />
            <FileIcon path={f.filename ?? ""} type="file" /><span className="truncate">{f.folder ? `${f.folder}/` : ""}{f.filename}</span><span className="ml-auto shrink-0 text-[11px] text-subtle">{bytes(f.size_bytes)}</span></label>))}
      </div>
      <div className="mt-3 flex justify-end gap-2"><Button variant="ghost" onClick={onClose}>{L("Cancelar", "Cancel")}</Button>
        <Button variant="primary" loading={busy} disabled={!pick.size} onClick={async () => { setBusy(true); try { await api(`/api/v1/workspaces/${wid}/import`, { method: "POST", json: { file_ids: [...pick], folder } }); onDone(); } catch (e: any) { push("error", e.message); } finally { setBusy(false); } }}>{L(`Copiar ${pick.size}`, `Copy ${pick.size}`)}</Button></div>
    </Modal>
  );
}
