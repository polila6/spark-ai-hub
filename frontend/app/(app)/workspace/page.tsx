"use client";
// Workspace: a persistent remote folder per user with files, an editor with previews, a sandboxed terminal and an
// assistant that writes, runs and fixes code in it (workspace tools ws_*).
// Desktop: three resizable zones remembered in this browser — LEFT files + terminal, CENTER the chat (the main
// focus), RIGHT the file viewer/editor (only there while a file is open). Both side panels collapse to a thin rail.
// Narrow screens: one column with Files | Chat | Viewer tabs.
import {
  Download, FileCode, FilePlus2, FileText, FolderOpen, FolderPlus, HardDriveDownload, Info, LifeBuoy, Loader2, PanelLeftClose, PanelRightClose,
  RefreshCw, Search, Sparkles, SquareTerminal, Upload, X,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import useSWR from "swr";
import { GuideBody } from "@/components/guide";
import { Button, Field, Input, Modal, Notice, Progress, Select, Tip, cx } from "@/components/ui";
import { Assistant } from "@/components/workspace/assistant";
import {
  type Draft, type Entry, FileIcon, type Run, RUNNABLE, Splitter, type Ws, baseName, ext, parentOf, rawUrl, useConfirm, usePersisted,
} from "@/components/workspace/common";
import { PanelHeader, Rail, ZoneTabs, useWidth } from "@/components/workspace/layout";
import { ManageDialog, WorkspaceSwitcher, WsDialog } from "@/components/workspace/switcher";
import { Terminal } from "@/components/workspace/terminal";
import { FileTree, type TreeAction } from "@/components/workspace/tree";
import { Viewer } from "@/components/workspace/viewer";
import { api, upload } from "@/lib/api";
import { bytes } from "@/lib/format";
import { GUIDES } from "@/lib/guides";
import { useI18n, useL } from "@/lib/i18n";
import { useToast } from "@/lib/providers";

/** Remembered layout: widths of the side panels, height of the terminal and which panels are open. */
type Layout = { left: number; right: number; term: number; leftOpen: boolean; termOpen: boolean; rightCollapsed: boolean };
const LAYOUT: Layout = { left: 288, right: 520, term: 240, leftOpen: true, termOpen: true, rightCollapsed: false };
const LEFT_MIN = 200, LEFT_MAX = 560, RIGHT_MIN = 320, CHAT_MIN = 360;
/** Below this width of the workspace area the three zones become tabs. */
const NARROW = 820;
const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v));
const LAST_KEY = "sah-ws-last";

export default function WorkspacePage() {
  const L = useL();
  const { push } = useToast();
  const { data, mutate } = useSWR("/api/v1/workspaces");
  const list: Ws[] = data?.workspaces ?? [];
  const [wid, setWid] = useState<string | null>(null);
  const ws = list.find((w) => w.id === wid) ?? list[0];
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
  // left part of the sticky top bar: which workspace + how much space it uses
  const header = (
    <>
      <WorkspaceSwitcher list={list} current={ws} onSwitch={switchTo} onNew={() => setWsDialog({})} onRename={(w) => setWsDialog({ w })} onManage={() => setManage(true)} onDelete={deleteWs} />
      {ws && limits && (
        <div className="hidden items-center gap-2 whitespace-nowrap text-[11px] text-muted md:flex" title={L("Espacio usado en este workspace", "Space used in this workspace")}>
          <Progress value={(used / max) * 100} tone={used / max > 0.9 ? "danger" : used / max > 0.75 ? "warn" : "accent"} className="w-24" />
          <span className="tabular-nums">{bytes(used)} / {limits.max_gb} GB</span>
          <Tip text={L(`Cada comando puede durar hasta ${Math.round(limits.timeout_s / 60)} min. ${limits.packages ? "Sin internet salvo para instalar paquetes con pip/npm." : "Sin internet."} Todo se ejecuta aislado en el Spark.`, `Each command can run for up to ${Math.round(limits.timeout_s / 60)} min. ${limits.packages ? "No internet except to install packages with pip/npm." : "No internet."} Everything runs isolated on the Spark.`)}>
            <button type="button" aria-label={L("Límites", "Limits")} className="text-subtle hover:text-muted"><Info className="h-3.5 w-3.5" /></button>
          </Tip>
        </div>
      )}
    </>
  );
  return (
    <div className="h-fill flex min-h-[420px] min-w-0 flex-col gap-1.5 max-sm:min-h-0 sm:gap-2">
      {!ws
        ? <>
            <div className="flex shrink-0 items-center gap-2 rounded-2xl border border-border bg-surface px-2.5 py-2 shadow-card">{header}</div>
            <div className="grid flex-1 place-items-center"><Loader2 className="h-5 w-5 animate-spin text-muted" /></div>
          </>
        : <WorkspaceIDE key={ws.id} ws={ws} limits={limits} header={header} onDirty={onDirty} onSizes={onSizes} />}
      {wsDialog && <WsDialog w={wsDialog.w} onClose={() => setWsDialog(null)} onSubmit={saveWs} />}
      {manage && <ManageDialog list={list} current={ws} maxGb={limits?.max_gb} onClose={() => setManage(false)} onOpen={(id) => { setManage(false); switchTo(id); }}
        onNew={() => setWsDialog({})} onRename={(w) => setWsDialog({ w })} onDelete={deleteWs} />}
      {confirmElement}
    </div>
  );
}

/** Which zone shows on narrow screens. */
type Zone = "files" | "chat" | "viewer" | "term";
type NameDialog = { kind: "file" | "dir" | "rename" | "move"; entry?: Entry; folder: string; value: string };

function WorkspaceIDE({ ws, limits, header, onDirty, onSizes }: {
  ws: Ws; limits: any; header: React.ReactNode; onDirty: (n: number) => void; onSizes: () => void;
}) {
  const L = useL();
  const { lang } = useI18n();
  const { push } = useToast();
  const [layout, setLayout] = usePersisted<Layout>("sah-ws-layout2", LAYOUT);
  const box = useRef<HTMLDivElement>(null);
  const boxW = useWidth(box);
  const narrow = boxW !== null && boxW < NARROW;
  const narrowRef = useRef(narrow);
  narrowRef.current = narrow;
  const [zone, setZone] = useState<Zone>("chat");
  const [guideOpen, setGuideOpen] = useState(false);
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
  const [dialog, setDialog] = useState<NameDialog | null>(null);
  const [importTo, setImportTo] = useState<string | null>(null);
  const [uploading, setUploading] = useState<{ done: number; total: number } | null>(null);
  const [dropEditor, setDropEditor] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const uploadTo = useRef("");
  const saveRef = useRef<(() => Promise<void>) | null>(null);
  const leftCol = useRef<HTMLElement>(null);
  const wasRunning = useRef<Set<string>>(new Set());
  const pruned = useRef(false);
  const { confirm, confirmElement } = useConfirm();
  const dirtyCount = Object.keys(drafts).length;
  const active = open.active && open.tabs.includes(open.active) ? open.active : null;
  const hasFiles = open.tabs.length > 0;
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
  // opening a file expands the viewer (desktop) or switches to the Viewer tab (narrow)
  const openFile = useCallback((p: string) => {
    setOpen((o) => ({ tabs: o.tabs.includes(p) ? o.tabs : [...o.tabs, p], active: p }));
    setSel(p); expandTo(p);
    setLayout((l) => (l.rightCollapsed ? { ...l, rightCollapsed: false } : l));
    if (narrowRef.current) setZone("viewer");
  }, [setOpen, setLayout]);
  // a link to a file from a chat answer elsewhere (/workspace#open=path) opens it here
  useEffect(() => {
    const m = window.location.hash.match(/^#open=(.+)$/);
    if (m) { openFile(decodeURIComponent(m[1])); window.history.replaceState(null, "", window.location.pathname); }
  }, [openFile]);
  const showFiles = () => { setLayout((l) => (l.leftOpen ? l : { ...l, leftOpen: true })); if (narrowRef.current) setZone("files"); };
  const reveal = (dir: string) => { setSel(dir || null); if (dir) expandTo(dir, true); showFiles(); };
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
      if (e.key === "`") {
        e.preventDefault();
        setLayout((l) => ({ ...l, termOpen: !l.termOpen, leftOpen: l.termOpen ? l.leftOpen : true }));
        if (narrowRef.current) setZone("term");
      }
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [active, setLayout]);

  async function run(command: string): Promise<boolean> {
    try {
      await api(`${base}/run`, { method: "POST", json: { command, background: true } });
      await mutRuns();
      setLayout((l) => (l.termOpen && l.leftOpen ? l : { ...l, termOpen: true, leftOpen: true }));
      if (narrowRef.current) setZone("term");
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

  // ------------------------------------------------------------------ layout
  const patch = (p: Partial<Layout>) => setLayout((l) => ({ ...l, ...p }));
  const width = () => box.current?.clientWidth ?? 1200;
  const termActions = (
    <button type="button" className="rounded p-1 text-[#8b949e] hover:bg-[#30363d] hover:text-[#e6edf3]" title={L("Ocultar el terminal (Ctrl+`)", "Hide the terminal (Ctrl+`)")} onClick={() => patch({ termOpen: false })}><X className="h-3.5 w-3.5" /></button>
  );
  const lastRun = runs[runs.length - 1];
  const sameName = (p: string) => open.tabs.some((t) => t !== p && baseName(t) === baseName(p));
  const ib = "!h-7 !w-7";
  const dirFor = () => (targetDir ? byPath.get(targetDir) ?? null : null);
  // what is visible: on narrow screens only the selected zone; on desktop the viewer exists only while a file is open
  const showLeft = narrow ? zone === "files" || zone === "term" : layout.leftOpen;
  // on phones the file tree and the terminal are separate tabs; on desktop they share the left column
  const treeShown = !narrow || zone === "files";
  const termShown = narrow ? zone === "term" : layout.termOpen;
  const leftRail = !narrow && !layout.leftOpen;
  const showChat = narrow ? zone === "chat" : true;
  const showRight = narrow ? zone === "viewer" : hasFiles && !layout.rightCollapsed;
  const rightRail = !narrow && hasFiles && layout.rightCollapsed;
  const guide = GUIDES.workspace;
  const toggle = (on: boolean, onClick: () => void, icon: React.ReactNode, label: string, disabled = false) => (
    <button type="button" onClick={onClick} aria-pressed={on} title={label} aria-label={label} disabled={disabled}
      className={cx("grid h-8 w-8 place-items-center rounded-lg transition disabled:cursor-not-allowed disabled:opacity-40 [&>svg]:h-4 [&>svg]:w-4",
        on ? "bg-accent/12 text-accent" : "text-muted hover:bg-elevated hover:text-fg")}>{icon}</button>
  );

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-2">
      {/* sticky top bar: workspace name + size, global actions, panel toggles */}
      <div className="z-10 flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border bg-surface px-2 py-1.5 sm:rounded-2xl sm:border sm:px-2.5 sm:py-2 sm:shadow-card">
        {header}
        <div className="ml-auto flex items-center gap-1">
          <Button size="sm" variant="ghost" className="!h-8" onClick={() => setGuideOpen((g) => !g)} aria-expanded={guideOpen} title={L("Cómo se usa el workspace", "How the workspace works")}>
            <LifeBuoy className="h-4 w-4 text-accent" /><span className="hidden lg:inline">{L("¿Cómo se usa?", "How does it work?")}</span>
          </Button>
          <Button size="icon" variant="ghost" className="!h-8 !w-8" title={L("Descargar todo el workspace (ZIP)", "Download the whole workspace (ZIP)")} onClick={() => action("download", null)}><Download className="h-4 w-4" /></Button>
          {!narrow && (
            <div className="ml-1 flex items-center gap-0.5 rounded-xl border border-border bg-bg/60 p-0.5" role="group" aria-label={L("Paneles", "Panels")}>
              {toggle(layout.leftOpen, () => patch({ leftOpen: !layout.leftOpen }), <FolderOpen />, L("Mostrar/ocultar archivos", "Show/hide files"))}
              {toggle(layout.termOpen && layout.leftOpen, () => patch({ termOpen: !layout.termOpen, leftOpen: layout.termOpen ? layout.leftOpen : true }), <SquareTerminal />, L("Mostrar/ocultar terminal (Ctrl+`)", "Show/hide terminal (Ctrl+`)"))}
              {toggle(hasFiles && !layout.rightCollapsed, () => patch({ rightCollapsed: !layout.rightCollapsed }), <FileCode />, hasFiles ? L("Mostrar/ocultar visor de archivos", "Show/hide file viewer") : L("Visor de archivos: se abre al elegir un archivo", "File viewer: opens when you pick a file"), !hasFiles)}
            </div>
          )}
        </div>
      </div>
      {guideOpen && guide && (
        <section className="max-h-[40vh] shrink-0 overflow-y-auto rounded-2xl border border-accent/25 bg-accent/[0.04] px-4 py-3" aria-label={lang === "en" ? "Guide" : "Guía"}>
          <GuideBody g={guide} />
        </section>
      )}
      {limits && !limits.code_enabled && <Notice tone="warn">{L("La ejecución de código está desactivada: puedes editar archivos, pero el terminal y el asistente no podrán ejecutar nada. Un administrador puede activarla en Ajustes.", "Code execution is disabled: you can edit files, but the terminal and the assistant cannot run anything. An administrator can enable it in Settings.")}</Notice>}

      {/* the three zones */}
      <div ref={box} className="flex min-h-[300px] min-w-0 flex-1 flex-col overflow-hidden border-y border-border bg-surface max-sm:min-h-0 sm:min-h-[360px] sm:rounded-2xl sm:border sm:shadow-card">
        {narrow && (
          <ZoneTabs value={zone} onChange={setZone} label={L("Zonas del workspace", "Workspace zones")} tabs={[
            { id: "chat", label: L("Chat", "Chat"), icon: <Sparkles /> },
            { id: "files", label: L("Archivos", "Files"), icon: <FolderOpen /> },
            { id: "viewer", label: L("Editor", "Editor"), icon: <FileCode />, badge: dirtyCount || undefined },
            { id: "term", label: L("Terminal", "Terminal"), icon: <SquareTerminal />, badge: running || undefined },
          ]} />
        )}
        <div className="flex min-h-0 flex-1">
          {/* LEFT: files + terminal */}
          {leftRail && <Rail side="left" expandLabel={L("Mostrar archivos", "Show files")} onExpand={() => patch({ leftOpen: true })} items={[
            { key: "files", icon: <FolderOpen />, label: L("Archivos", "Files"), onClick: () => patch({ leftOpen: true }) },
            { key: "term", icon: running ? <Loader2 /> : <SquareTerminal />, spin: running > 0, label: L("Terminal", "Terminal"), badge: running || undefined, onClick: () => patch({ leftOpen: true, termOpen: true }) },
          ]} />}
          {showLeft && (
            <aside ref={leftCol} aria-label={L("Archivos", "Files")} style={narrow ? undefined : { width: layout.left }}
              className={cx("flex min-h-0 min-w-0 flex-col", narrow ? "flex-1" : "min-w-[200px] shrink bg-elevated/20")}>
              {treeShown && <>
              <PanelHeader icon={<FolderOpen />} title={L("Archivos", "Files")}>
                <Button size="icon" variant="ghost" className={ib} title={L(`Nuevo archivo en /${targetDir}`, `New file in /${targetDir}`)} onClick={() => action("newfile", dirFor())}><FilePlus2 className="h-4 w-4" /></Button>
                <Button size="icon" variant="ghost" className={ib} title={L(`Nueva carpeta en /${targetDir}`, `New folder in /${targetDir}`)} onClick={() => action("newdir", dirFor())}><FolderPlus className="h-4 w-4" /></Button>
                <Button size="icon" variant="ghost" className={ib} title={L(`Subir archivos a /${targetDir} (o arrástralos a una carpeta)`, `Upload files to /${targetDir} (or drop them on a folder)`)} onClick={() => action("upload", dirFor())}><Upload className="h-4 w-4" /></Button>
                <Button size="icon" variant="ghost" className={ib} title={L("Traer desde Mis archivos", "Bring from My files")} onClick={() => setImportTo(targetDir)}><HardDriveDownload className="h-4 w-4" /></Button>
                <Button size="icon" variant="ghost" className={ib} title={L("Actualizar", "Refresh")} onClick={refresh}><RefreshCw className="h-3.5 w-3.5" /></Button>
                {!narrow && <Button size="icon" variant="ghost" className={ib} title={L("Ocultar archivos", "Hide files")} onClick={() => patch({ leftOpen: false })}><PanelLeftClose className="h-4 w-4" /></Button>}
              </PanelHeader>
              <div className="shrink-0 px-2 pt-2">
                <div className="relative">
                  <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-subtle" />
                  <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={L("Buscar archivo…", "Find a file…")} aria-label={L("Buscar archivo", "Find a file")}
                    onKeyDown={(e) => { if (e.key === "Escape") setFilter(""); }}
                    className="h-8 w-full rounded-lg border border-border bg-bg pl-7 pr-6 text-xs text-fg placeholder:text-subtle focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25" />
                  {filter && <button type="button" className="absolute right-1.5 top-1/2 -translate-y-1/2 text-subtle hover:text-fg" onClick={() => setFilter("")} aria-label={L("Borrar búsqueda", "Clear search")}><X className="h-3.5 w-3.5" /></button>}
                </div>
              </div>
              <div className="min-h-[96px] flex-1 overflow-y-auto p-1.5">
                {!tree ? <Loader2 className="m-3 h-4 w-4 animate-spin text-muted" />
                  : <>
                    <FileTree entries={entries} selected={sel} active={active} closed={closed} filter={filter}
                      onToggle={(p) => setClosed((c) => { const n = new Set(c); if (n.has(p)) n.delete(p); else n.add(p); return n; })}
                      onSelect={(e) => setSel(e.path)} onAction={action} onDropFiles={uploadFiles} onMove={move} />
                    {entries.length === 0 && (
                      <div className="mx-2 mt-4 rounded-xl border border-dashed border-border px-3 py-5 text-center text-xs text-muted">
                        <Upload className="mx-auto mb-2 h-5 w-5 text-subtle" />
                        {L("Carpeta vacía. Arrastra archivos aquí, usa ⬆ o pídele al asistente que cree algo.", "Empty folder. Drop files here, use ⬆ or ask the assistant to create something.")}
                      </div>
                    )}
                  </>}
              </div>
              <div className="shrink-0 border-t border-border px-3 py-1.5 text-[10.5px] text-subtle">
                {uploading ? <span className="flex items-center gap-1.5 text-info"><Loader2 className="h-3 w-3 animate-spin" />{L(`Subiendo ${uploading.done + 1} de ${uploading.total}…`, `Uploading ${uploading.done + 1} of ${uploading.total}…`)}</span>
                  : <span className="block truncate" title={L("Lo nuevo (archivos, carpetas, subidas) va a esta carpeta. Selecciona otra carpeta para cambiarla.", "New items (files, folders, uploads) go to this folder. Select another folder to change it.")}>
                      {L("Destino", "Target")}: /{targetDir} · {L("clic derecho para más acciones", "right click for more actions")}</span>}
              </div>
              </>}
              <input ref={fileInput} type="file" multiple className="hidden" onChange={(e) => { if (e.target.files) uploadFiles(uploadTo.current, e.target.files); e.target.value = ""; }} />

              {/* terminal (collapsible) */}
              {termShown ? <>
                {!narrow && <Splitter dir="y" label={L("Cambiar la altura del terminal", "Resize the terminal")} onReset={() => patch({ term: LAYOUT.term })}
                  onDelta={(d) => setLayout((l) => ({ ...l, term: clamp(l.term - d, 110, Math.max(160, (leftCol.current?.clientHeight ?? 600) - 200)) }))} />}
                <div className={cx("min-h-0", narrow ? "flex-1" : "shrink-0")} style={narrow ? undefined : { height: layout.term, maxHeight: "70%" }}>
                  <Terminal runs={runs} onRun={run} onStop={stopRun} onOpen={openFile} codeEnabled={!!limits?.code_enabled} timeoutMin={limits ? Math.round(limits.timeout_s / 60) : undefined} packages={limits?.packages}
                    onClear={async () => { try { await api(`${base}/runs`, { method: "DELETE" }); await mutRuns(); } catch (e: any) { push("error", e.message); } }} actions={termActions} />
                </div>
              </> : narrow ? null : (
                <button type="button" onClick={() => patch({ termOpen: true })} className="flex h-9 shrink-0 items-center gap-2 border-t border-border px-3 text-xs text-muted transition hover:bg-elevated hover:text-fg">
                  <SquareTerminal className="h-3.5 w-3.5" />{L("Terminal", "Terminal")}
                  {running > 0 ? <span className="flex items-center gap-1 text-info"><Loader2 className="h-3 w-3 animate-spin" />{L(`${running} en marcha`, `${running} running`)}</span>
                    : lastRun && <span className="min-w-0 truncate font-mono text-[11px] text-subtle">$ {lastRun.command.split("\n")[0]} · {lastRun.timed_out ? L("tiempo agotado", "timed out") : `exit ${lastRun.exit_code}`}</span>}
                  <span className="ml-auto shrink-0 text-[10.5px] text-subtle">Ctrl+`</span>
                </button>
              )}
            </aside>
          )}
          {showLeft && !narrow && <Splitter dir="x" label={L("Cambiar el ancho de los archivos", "Resize the files panel")} onReset={() => patch({ left: LAYOUT.left })}
            onDelta={(d) => setLayout((l) => ({ ...l, left: clamp(l.left + d, LEFT_MIN, Math.max(LEFT_MIN, Math.min(LEFT_MAX, width() - CHAT_MIN - (hasFiles && !l.rightCollapsed ? RIGHT_MIN : 0)))) }))} />}

          {/* CENTER: the chat */}
          <section aria-label={L("Asistente", "Assistant")} className={cx("flex min-h-0 min-w-0 flex-1 flex-col bg-surface", !narrow && "min-w-[320px]", !showChat && "hidden")}>
            <Assistant wid={ws.id} onFiles={refresh} onOpen={openFile} />
          </section>

          {/* RIGHT: viewer / editor / previews */}
          {showRight && !narrow && <Splitter dir="x" label={L("Cambiar el ancho del visor", "Resize the viewer")} onReset={() => patch({ right: LAYOUT.right })}
            onDelta={(d) => setLayout((l) => ({ ...l, right: clamp(l.right - d, RIGHT_MIN, Math.max(RIGHT_MIN, width() - CHAT_MIN - (l.leftOpen ? l.left : 44))) }))} />}
          {showRight && (
            <section aria-label={L("Visor de archivos", "File viewer")} style={narrow ? undefined : { width: layout.right }}
              className={cx("flex min-h-0 min-w-0 flex-col", narrow ? "flex-1" : "min-w-[300px] shrink bg-surface")}>
              {hasFiles && (
                <div className="flex h-11 shrink-0 items-end border-b border-border bg-elevated/40">
                  <div className="flex h-full min-w-0 flex-1 items-end gap-px overflow-x-auto px-1 pt-1.5" role="tablist" aria-label={L("Archivos abiertos", "Open files")}>
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
                  {!narrow && <Button size="icon" variant="ghost" className={cx(ib, "mx-1 mb-2 shrink-0")} title={L("Ocultar el visor", "Hide the viewer")} onClick={() => patch({ rightCollapsed: true })}><PanelRightClose className="h-4 w-4" /></Button>}
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
                          <li><span className="mr-1.5 font-semibold text-accent">1.</span>{L("Abre un archivo en la pestaña Archivos (clic derecho o ⋯ para renombrar, mover, descargar…). Puedes tener varios abiertos en pestañas.", "Open a file from the Files tab (right click or ⋯ to rename, move, download…). You can keep several open in tabs.")}</li>
                          <li><span className="mr-1.5 font-semibold text-accent">2.</span>{L(`Arrastra aquí tus datos para subirlos a /${targetDir}, o suéltalos sobre una carpeta concreta.`, `Drop your data here to upload it to /${targetDir}, or drop it on a specific folder.`)}</li>
                          <li><span className="mr-1.5 font-semibold text-accent">3.</span>{L("Ejecuta lo que quieras en el terminal (pestaña Archivos), o pídele al asistente que escriba y pruebe el código por ti.", "Run anything in the terminal (Files tab), or ask the assistant to write and test the code for you.")}</li>
                        </ol>
                        <div className="flex flex-wrap justify-center gap-2">
                          <Button size="sm" variant="outline" onClick={() => action("newfile", dirFor())}><FilePlus2 className="h-3.5 w-3.5" />{L("Nuevo archivo", "New file")}</Button>
                          <Button size="sm" variant="outline" onClick={() => action("upload", dirFor())}><Upload className="h-3.5 w-3.5" />{L("Subir archivos", "Upload files")}</Button>
                          {byPath.has("LEEME.md") && <Button size="sm" variant="ghost" onClick={() => openFile("LEEME.md")}>LEEME.md</Button>}
                        </div>
                      </div>
                    </div>}
              </div>
            </section>
          )}
          {rightRail && <Rail side="right" expandLabel={L("Mostrar el visor", "Show the viewer")} onExpand={() => patch({ rightCollapsed: false })}
            items={[{ key: "viewer", icon: <FileCode />, label: L("Visor de archivos", "File viewer"), badge: dirtyCount || undefined, onClick: () => patch({ rightCollapsed: false }) }]}
            vertical={active ? baseName(active) : undefined} />}
        </div>
      </div>

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
