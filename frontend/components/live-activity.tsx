"use client";
// "Live activity" panel of a chat answer (main chat and workspace assistant): what the assistant is doing while it works —
// its plan, its reasoning as it streams, a timeline of every tool call (live timer, result, details) and the status
// line (current step, context use, compactions, warnings). Expanded while running, a one-line summary when done.
// The reducers below turn the turn's SSE events into message fields; the panel is rebuilt the same way from replayed
// events (re-attach) and from the saved message (`parts`, `metrics.reasoning`). Events it does not know are ignored, so
// it degrades to tools + reasoning with an older backend.
import {
  Activity as ActivityIcon, Bot, Brain, CalendarClock, Check, ChevronDown, ChevronRight, Circle, Code2, Compass, FilePen, FileText, FolderOpen, Globe,
  Image as ImageIcon, ListChecks, Loader2, MessageSquarePlus, Minus, Puzzle, Search, Shrink, Table2, Telescope, Terminal, TriangleAlert, Wrench, X,
} from "lucide-react";
import { memo, useEffect, useMemo, useRef, useState } from "react";
import { AgentReport, CodeRun, kTok, WebSources } from "@/components/assistant";
import { directorText } from "@/components/effort";
import { cx } from "@/components/ui";
import { tr, useL } from "@/lib/i18n";

// The server names the tool it is running in Spanish; show the name in the page's language when we know it.
const RUNNING_LABELS: Record<string, [string, string]> = {
  generate_image: ["Generando imagen", "Generating image"], edit_image: ["Editando imagen", "Editing image"],
  search_knowledge: ["Buscando en tus documentos", "Searching your documents"], read_document: ["Leyendo documento", "Reading document"],
  describe_data: ["Analizando datos", "Analysing data"], query_data: ["Consultando datos", "Querying data"],
  save_memory: ["Guardando en memoria", "Saving to memory"], web_search: ["Buscando en internet", "Searching the web"],
  fetch_url: ["Leyendo página web", "Reading web page"], fetch_urls: ["Leyendo varias páginas", "Reading several pages"], http_request: ["Consultando una API", "Calling an API"], crawl_site: ["Rastreando un sitio", "Crawling a site"], search_images: ["Buscando imágenes", "Searching images"], extract_images: ["Buscando imágenes en la página", "Finding the page's images"], search_papers: ["Buscando artículos", "Searching papers"], doi_to_bibtex: ["Obteniendo referencias", "Getting references"], download_file: ["Descargando archivo", "Downloading file"], view_image: ["Mirando la imagen", "Looking at the image"], use_skill: ["Cargando skill", "Loading skill"],
  read_skill_file: ["Leyendo archivo de skill", "Reading skill file"], find_skills: ["Buscando skills", "Finding skills"],
  install_skill: ["Instalando skill", "Installing skill"], delegate_task: ["Agente trabajando", "Agent working"],
  compact_context: ["Contexto compactado", "Context compacted"], run_code: ["Ejecutando código", "Running code"],
  canvas_write: ["Escribiendo en el lienzo", "Writing to the canvas"], canvas_edit: ["Editando el lienzo", "Editing the canvas"],
  deep_research: ["Investigación a fondo", "Deep research"], schedule_task: ["Programando tarea", "Scheduling task"],
  ws_list: ["Mirando archivos", "Looking at files"], ws_read: ["Leyendo archivo", "Reading file"], ws_write: ["Escribiendo archivo", "Writing file"],
  ws_append: ["Añadiendo al archivo", "Appending to file"], ws_edit: ["Editando archivo", "Editing file"], ws_run: ["Ejecutando en el workspace", "Running in the workspace"],
  update_plan: ["Actualizando el plan", "Updating the plan"],
  db_schema: ["Mirando la base de datos", "Looking at the database"], db_query: ["Consultando la base de datos", "Querying the database"],
};
export const runningLabel = (t: { name?: string; label?: string }): string => {
  const k = RUNNING_LABELS[t.name ?? ""];
  return k ? tr(k[0], k[1]) : t.label || t.name || "";
};

export type PlanItem = { content: string; status: string };
export type ActivityNote = { kind: "compacted" | "warning" | "user"; text: string; at: number };  // at = number of tools before it
export type Activity = { plan?: PlanItem[]; step?: number; status?: string; phase?: "thinking" | "writing" | "tool"; ctx?: { used: number; limit?: number };
  notes?: ActivityNote[]; t0?: number; t1?: number; preparing?: { label: string; chars: number }; director?: any };
type LiveMsg = { tools?: any[]; reasoning?: string; activity?: Activity };

// `started` may come as epoch seconds, epoch ms or an ISO date.
export const toMs = (v: any): number | undefined => typeof v === "number" ? (v > 1e12 ? v : v > 1e9 ? v * 1000 : undefined) : typeof v === "string" ? Date.parse(v) || undefined : undefined;

const kChars = (n: number) => (n < 1000 ? `${n} caracteres` : `${(n / 1000).toFixed(1)} k caracteres`);

export function reduceTools(tools: any[] | undefined, ev: any): any[] | undefined {
  const list = tools ?? [];
  if (ev.type === "tool_start") return list.some((t) => t.id === ev.id) ? tools : [...list, { id: ev.id, name: ev.name, label: ev.label, args: ev.args, t0: toMs(ev.started) ?? Date.now() }];
  if (ev.type === "tool_progress") return list.map((t) => (t.id === ev.id ? { ...t, progress: ev.progress, message: ev.message ?? t.message, status: ev.status,
    log: ev.message && ev.message !== t.message ? [...(t.log ?? []).slice(-29), ev.message] : t.log } : t));
  if (ev.type === "tool_result") return list.map((t) => (t.id === ev.id ? { ...t, ...ev, done: true, t1: Date.now() } : t));
  return tools;
}

/** Fold one event into the activity (returns the same object when nothing changed, so memoized panels skip renders). */
export function reduceActivity(a: Activity | undefined, ev: any, toolCount: number): Activity | undefined {
  const x = a ?? {};
  const note = (kind: ActivityNote["kind"], text: string): Activity => ({ ...x, notes: [...(x.notes ?? []), { kind, text, at: toolCount }] });
  switch (ev.type) {
    case "plan": return Array.isArray(ev.items) ? { ...x, plan: ev.items } : a;
    case "step": return typeof ev.n === "number" && ev.n !== x.step ? { ...x, step: ev.n } : a;
    case "status": return ev.status === x.status ? a : { ...x, status: ev.status };
    case "reasoning": return x.phase === "thinking" && !x.preparing ? a : { ...x, phase: "thinking", preparing: undefined };
    case "delta": return x.phase === "writing" && !x.preparing ? a : { ...x, phase: "writing", preparing: undefined };
    case "tool_start": return x.phase === "tool" && !x.preparing ? a : { ...x, phase: "tool", preparing: undefined };
    case "tool_writing": return { ...x, phase: "tool", preparing: { label: ev.label || ev.name || "", chars: ev.chars ?? 0 } };
    case "context": return typeof ev.used === "number" ? { ...x, ctx: { used: ev.used, limit: ev.limit ?? x.ctx?.limit } } : a;
    case "context_compacted": return note("compacted", `${kTok(ev.before)} → ${kTok(ev.after)}`);
    case "warning": return ev.message ? note("warning", ev.message) : a;
    case "user_injected": return note("user", ev.content ?? "");
    case "director": return { ...x, director: ev };
    case "done": case "error": return { ...x, t1: Date.now() };
    default: return a;
  }
}

/** Apply a turn event to the running assistant message (tools, reasoning, activity); other fields are left to the page. */
export function applyLive<M extends LiveMsg>(m: M, ev: any): M {
  const tools = reduceTools(m.tools, ev);
  const reasoning = ev.type === "reasoning" ? (m.reasoning ?? "") + (ev.content ?? "") : m.reasoning;
  const activity = reduceActivity(m.activity, ev, (tools ?? []).length);
  return tools === m.tools && reasoning === m.reasoning && activity === m.activity ? m : { ...m, tools, reasoning, activity };
}

/** What a saved message can rebuild: tools from `parts`, the last stored plan, the reasoning text. */
export function liveFromSaved(m: any, label?: (name: string) => string | undefined): LiveMsg {
  const parts: any[] = m.parts ?? [];
  const plan = [...parts].reverse().find((p) => p.type === "plan");
  const tools = parts.filter((p) => p.type === "tool").map((p, i) => ({ id: `p${i}`, label: label?.(p.name), ...p, done: true }));
  const reasoning = m.reasoning ?? m.metrics?.reasoning ?? undefined;
  const items = plan?.items ?? plan?.plan;
  const activity: Activity | undefined = Array.isArray(items) || m.metrics?.steps || m.metrics?.director ? { plan: Array.isArray(items) ? items : undefined, step: m.metrics?.steps, director: m.metrics?.director } : undefined;
  return { tools, reasoning: reasoning || undefined, activity };
}

// ------------------------------------------------------------------ helpers

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { if (!active) return; setNow(Date.now()); const h = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(h); }, [active]);
  return now;
}

const dur = (s?: number | null) => (s == null || !isFinite(s) ? "" : s < 10 ? `${s.toFixed(1)} s` : s < 60 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`);

function toolIcon(name = "") {
  if (name === "web_search" || name === "fetch_url" || name === "search_images" || name === "fetch_urls" || name === "http_request" || name === "crawl_site" || name === "download_file" || name === "extract_images" || name === "search_papers" || name === "doi_to_bibtex") return Globe;
  if (name === "ws_write" || name === "ws_append" || name === "ws_edit" || name.startsWith("canvas_")) return FilePen;
  if (name === "ws_read" || name === "read_document" || name === "read_skill_file") return FileText;
  if (name === "ws_list") return FolderOpen;
  if (name === "ws_run") return Terminal;
  if (name === "run_code") return Code2;
  if (name === "delegate_task") return Bot;
  if (name === "deep_research") return Telescope;
  if (name.includes("skill")) return Puzzle;
  if (name === "compact_context") return Shrink;
  if (name.includes("image")) return ImageIcon;
  if (name.includes("data")) return Table2;
  if (name === "search_knowledge") return Search;
  if (name === "save_memory") return Brain;
  if (name === "schedule_task") return CalendarClock;
  return Wrench;
}

/** The argument that says most about a call: path, first line of the command, query, URL, agent task… */
function keyArg(t: any): string {
  const a = t.args ?? {};
  const v = a.path ?? a.command ?? a.query ?? a.url ?? (a.agent ? `${a.agent}: ${a.task ?? ""}` : undefined) ?? a.task ?? a.name ?? a.skill ?? a.question ?? a.prompt ?? a.sql ?? a.title;
  return typeof v === "string" ? v.split("\n")[0].slice(0, 160) : "";
}

const fmtArgs = (args: any) => { try { return JSON.stringify(args, (_k, v) => (typeof v === "string" && v.length > 3000 ? `${v.slice(0, 3000)}… (+${v.length - 3000})` : v), 2); } catch { return String(args); } };

function toolSeconds(t: any): number | undefined {
  if (typeof t.seconds === "number") return t.seconds;
  if (typeof t.terminal?.seconds === "number") return t.terminal.seconds;
  if (t.t0 && t.t1 && t.t1 - t.t0 > 50) return (t.t1 - t.t0) / 1000;  // replayed events arrive together: no real duration
  return undefined;
}

// ------------------------------------------------------------------ pieces

const StepRow = memo(function StepRow({ t, n, now, live, onOpen }: { t: any; n: number; now: number; live: boolean; onOpen?: (p: string) => void }) {
  const L = useL();
  const [open, setOpen] = useState(false);
  const running = !t.done && live;
  const Icon = toolIcon(t.name);
  const arg = keyArg(t);
  const secs = running ? (t.t0 ? (now - t.t0) / 1000 : undefined) : toolSeconds(t);
  const changed: string[] = t.workspace?.changed ?? [];
  const removed: string[] = t.workspace?.removed ?? [];
  const webSources = (t.sources ?? []).filter((s: any) => s.url);
  const docSources = (t.sources ?? []).filter((s: any) => !s.url);
  const subLog = running && (t.name === "delegate_task" || t.name === "deep_research") && (t.log?.length ?? 0) > 0;
  return (
    <li className={cx("rounded-lg", running && "bg-info/5")}>
      <button type="button" className="flex w-full items-start gap-2 px-2 py-1.5 text-left hover:bg-elevated/60" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className="w-5 shrink-0 pt-0.5 text-right font-mono text-[10px] text-subtle">{n}</span>
        <span className={cx("mt-px grid h-5 w-5 shrink-0 place-items-center rounded-md", running ? "bg-info/15 text-info" : !t.done ? "bg-elevated text-subtle" : t.ok === false ? "bg-warn/15 text-warn" : "bg-accent/12 text-accent")}><Icon className="h-3 w-3" /></span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-1.5"><span className="shrink-0 font-medium text-fg">{runningLabel(t)}</span>{arg && <span className="truncate font-mono text-[10.5px] text-muted">{arg}</span>}</span>
          {(running ? t.message : t.summary) && <span className={cx("block truncate text-[11px]", t.ok === false ? "text-warn" : "text-subtle")}>{running ? t.message : t.summary}</span>}
          {!t.done && !live && <span className="block text-[11px] text-subtle">{L("Interrumpido", "Interrupted")}</span>}
        </span>
        <span className="flex shrink-0 items-center gap-1 pt-0.5 text-[10.5px] tabular-nums text-subtle">
          {secs != null && dur(secs)}
          {running ? <Loader2 className="h-3 w-3 animate-spin text-info" /> : !t.done ? <Minus className="h-3 w-3" /> : t.ok === false ? <X className="h-3 w-3 text-warn" /> : <Check className="h-3 w-3 text-ok" />}
          {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        </span>
      </button>
      {running && t.progress != null && t.progress > 0 && <div className="mx-9 mb-1 h-1 overflow-hidden rounded bg-elevated"><div className="h-full bg-info transition-all" style={{ width: `${Math.round(t.progress * 100)}%` }} /></div>}
      {subLog && <ul className="mb-1 ml-9 space-y-0.5 text-[10.5px] text-subtle">{t.log.slice(-5).map((x: string, i: number) => <li key={i} className="truncate">· {x}</li>)}</ul>}
      {changed.length > 0 && !open && <div className="mb-1 ml-9 flex flex-wrap gap-1">{changed.slice(0, 6).map((p) => <FileChip key={p} p={p} onOpen={onOpen} />)}{changed.length > 6 && <span className="text-[10.5px] text-subtle">+{changed.length - 6}</span>}</div>}
      {open && <div className="mb-2 ml-9 mr-2 space-y-1.5 rounded-lg border border-border/60 bg-bg/70 p-2 text-[11px]">
        {t.args && Object.keys(t.args).length > 0 && <details><summary className="cursor-pointer text-muted">{L("Argumentos", "Arguments")}</summary><pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap break-words font-mono text-[10.5px]">{fmtArgs(t.args)}</pre></details>}
        {t.terminal && <div>
          <p className="truncate font-mono text-muted">$ {String(t.terminal.command ?? "").split("\n")[0].slice(0, 200)}
            <span className={cx("ml-2", t.terminal.exit_code ? "text-warn" : "text-ok")}>exit {t.terminal.exit_code ?? "?"}</span>{t.terminal.seconds != null && <span className="ml-2 text-subtle">{dur(t.terminal.seconds)}</span>}</p>
          {(t.terminal.stdout || t.terminal.stderr) && <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap rounded bg-[#0d1117] p-2 font-mono text-[10.5px] text-[#c9d1d9]">{t.terminal.stdout}{t.terminal.stderr ? <span className="text-[#ff7b72]">{t.terminal.stdout ? "\n" : ""}{t.terminal.stderr}</span> : null}</pre>}
        </div>}
        {(changed.length > 0 || removed.length > 0) && <div className="flex flex-wrap items-center gap-1"><span className="text-muted">{L("Archivos", "Files")}:</span>
          {changed.map((p) => <FileChip key={p} p={p} onOpen={onOpen} />)}{removed.map((p) => <span key={p} className="rounded border border-border px-1.5 font-mono text-[10.5px] text-subtle line-through">{p}</span>)}</div>}
        {t.agent && <div className="-mx-2 -mb-2"><AgentReport agent={t.agent} /></div>}
        {t.code && <div className="-mx-2"><CodeRun run={t.code} /></div>}
        {t.research?.subquestions?.length > 0 && <ol className="list-decimal space-y-0.5 pl-5 text-subtle">{t.research.subquestions.map((q: string, i: number) => <li key={i}>{q}</li>)}</ol>}
        {t.sql && <pre className="overflow-x-auto whitespace-pre-wrap font-mono text-[10.5px]">{t.sql}</pre>}
        {webSources.length > 0 && <div className="-mx-2"><WebSources sources={webSources} /></div>}
        {docSources.length > 0 && <ul className="space-y-1">{docSources.map((s: any, i: number) => <li key={i}><span className="font-medium text-fg">[{i + 1}] {s.filename}{s.page ? `, p. ${s.page}` : ""}</span>{s.snippet && <p className="line-clamp-2 text-muted">{s.snippet}</p>}</li>)}</ul>}
        {(t.log?.length ?? 0) > 0 && <details><summary className="cursor-pointer text-muted">{L("Progreso", "Progress")} ({t.log.length})</summary><ul className="mt-1 space-y-0.5 text-subtle">{t.log.map((x: string, i: number) => <li key={i} className="break-words">· {x}</li>)}</ul></details>}
        {t.ok === false && t.remediation && <p className="text-warn">{t.remediation}</p>}
        {!t.done && <p className="text-subtle">{live ? L("En curso…", "Running…") : L("No terminó (respuesta detenida o interrumpida)", "Did not finish (answer stopped or interrupted)")}</p>}
      </div>}
    </li>
  );
});

function FileChip({ p, onOpen }: { p: string; onOpen?: (p: string) => void }) {
  return onOpen ? <button type="button" className="rounded border border-border px-1.5 font-mono text-[10.5px] text-fg hover:border-accent hover:text-accent" onClick={(e) => { e.stopPropagation(); onOpen(p); }}>{p}</button>
    : <span className="rounded border border-border px-1.5 font-mono text-[10.5px]">{p}</span>;
}

function NoteRow({ n }: { n: ActivityNote }) {
  const L = useL();
  const Icon = n.kind === "warning" ? TriangleAlert : n.kind === "compacted" ? Shrink : MessageSquarePlus;
  return (
    <li className={cx("ml-7 flex items-start gap-2 rounded-lg px-2 py-1 text-[11px]", n.kind === "warning" ? "text-warn" : n.kind === "user" ? "bg-accent/8 text-fg" : "text-muted")}>
      <Icon className="mt-px h-3 w-3 shrink-0" />
      <span className="min-w-0 break-words">{n.kind === "compacted" ? L(`Contexto comprimido: ${n.text}`, `Context compacted: ${n.text}`) : n.kind === "user" ? <><span className="font-medium">{L("Añadido a la tarea", "Added to the task")}:</span> {n.text.length > 300 ? `${n.text.slice(0, 300)}…` : n.text}</> : n.text}</span>
    </li>
  );
}

function Thinking({ text, live }: { text: string; live: boolean }) {
  const L = useL();
  const [open, setOpen] = useState<boolean | null>(null);
  const [all, setAll] = useState(false);
  const box = useRef<HTMLPreElement>(null);
  const stick = useRef(true);
  const isOpen = open ?? live;
  const CAP = 12000;
  const shown = all || text.length <= CAP ? text : text.slice(-CAP);
  useEffect(() => { const el = box.current; if (el && stick.current) el.scrollTop = el.scrollHeight; }, [shown, isOpen]);
  return (
    <div className="border-t border-border/60 px-3 py-2">
      <button type="button" className="flex w-full items-center gap-1.5 text-left text-muted" onClick={() => setOpen(!isOpen)} aria-expanded={isOpen}>
        <Brain className={cx("h-3.5 w-3.5", live && "text-info")} /><span className="font-medium">{live ? L("Pensando…", "Thinking…") : L("Razonamiento", "Reasoning")}</span>
        <span className="text-[10.5px] text-subtle">~{kTok(Math.round(text.length / 4))} tok</span>
        {isOpen ? <ChevronDown className="ml-auto h-3 w-3" /> : <ChevronRight className="ml-auto h-3 w-3" />}
      </button>
      {isOpen && <>
        {shown.length < text.length && <button type="button" className="mt-1 text-[10.5px] text-accent" onClick={() => setAll(true)}>{L(`Ver todo (${kTok(text.length)} caracteres)`, `Show all (${kTok(text.length)} characters)`)}</button>}
        <pre ref={box} onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40; }}
          className="mt-1 max-h-56 overflow-y-auto whitespace-pre-wrap break-words font-mono text-[11px] italic leading-relaxed text-muted">{shown}</pre>
      </>}
    </div>
  );
}

function Plan({ items }: { items: PlanItem[] }) {
  const L = useL();
  const done = items.filter((i) => i.status === "completed").length;
  return (
    <div className="border-t border-border/60 px-3 py-2">
      <p className="mb-1 flex items-center gap-1.5 text-muted"><ListChecks className="h-3.5 w-3.5" /><span className="font-medium">{L("Plan", "Plan")}</span><span className="text-[10.5px] tabular-nums text-subtle">{done}/{items.length}</span>
        <span className="ml-1 h-1 w-20 overflow-hidden rounded bg-elevated"><span className="block h-full bg-ok transition-all" style={{ width: `${items.length ? (done / items.length) * 100 : 0}%` }} /></span></p>
      <ul className="space-y-0.5">{items.map((it, i) => (
        <li key={i} className={cx("flex items-start gap-1.5 rounded px-1 py-0.5", it.status === "in_progress" && "bg-info/10 font-medium text-fg", it.status === "completed" && "text-subtle line-through decoration-subtle/50", it.status === "pending" && "text-muted")}>
          {it.status === "completed" ? <Check className="mt-px h-3 w-3 shrink-0 text-ok" /> : it.status === "in_progress" ? <ChevronRight className="mt-px h-3 w-3 shrink-0 text-info" /> : <Circle className="mt-px h-3 w-3 shrink-0 text-subtle" />}
          <span className="min-w-0 break-words">{it.content}</span></li>))}</ul>
    </div>
  );
}

// ------------------------------------------------------------------ the panel

type Props = { tools?: any[]; reasoning?: string; activity?: Activity; running: boolean; seconds?: number; queued?: number; onOpen?: (p: string) => void };
const STEP_PAGE = 100;

function LiveActivityInner({ tools: toolsIn, reasoning, activity: a, running, seconds, queued = 0, onOpen }: Props) {
  const L = useL();
  const tools = useMemo(() => toolsIn ?? [], [toolsIn]);
  const runningTools = running ? tools.filter((t) => !t.done) : [];
  const now = useNow(running);
  const [open, setOpen] = useState<boolean | null>(null);
  useEffect(() => { if (!running) setOpen(null); }, [running]);  // collapses to the summary when the answer finishes
  const isOpen = open ?? running;
  const [shown, setShown] = useState(STEP_PAGE);
  const notesIn = a?.notes;
  const notes = useMemo(() => notesIn ?? [], [notesIn]);
  const plan = a?.plan ?? [];
  // the timeline: tools in order, with the notes (warnings, compactions, added messages) where they happened
  const rows = useMemo(() => {
    const byAt = new Map<number, ActivityNote[]>();
    for (const n of notes) byAt.set(n.at, [...(byAt.get(n.at) ?? []), n]);
    const out: ({ k: "t"; t: any; n: number } | { k: "n"; n: ActivityNote })[] = [];
    tools.forEach((t, i) => { for (const n of byAt.get(i) ?? []) out.push({ k: "n", n }); out.push({ k: "t", t, n: i + 1 }); });
    for (const [at, ns] of byAt) if (at >= tools.length) for (const n of ns) out.push({ k: "n", n });
    return out;
  }, [tools, notes]);
  if (!running && !tools.length && !reasoning && !plan.length && !notes.length) return null;
  const hidden = Math.max(0, rows.length - shown);
  const failed = tools.filter((t) => t.done && t.ok === false).length;
  const warnings = notes.filter((n) => n.kind === "warning").length;
  const planDone = plan.filter((i) => i.status === "completed").length;
  const total = seconds ?? (a?.t0 ? ((running ? now : a.t1 ?? now) - a.t0) / 1000 : undefined);
  const cur = runningTools[runningTools.length - 1];
  const statusText = cur ? `${runningLabel(cur)}${cur.t0 ? ` · ${dur((now - cur.t0) / 1000)}` : ""}${runningTools.length > 1 ? L(` (+${runningTools.length - 1} en paralelo)`, ` (+${runningTools.length - 1} in parallel)`) : ""}`
    : a?.status === "loading" ? L("Cargando el modelo…", "Loading the model…") : a?.status === "compacting" ? L("Comprimiendo el contexto…", "Compacting the context…")
    : a?.preparing ? L(`Preparando: ${a.preparing.label} · ${kChars(a.preparing.chars)}`, `Preparing: ${a.preparing.label} · ${kChars(a.preparing.chars)}`)
    : a?.phase === "thinking" ? L("Pensando…", "Thinking…") : a?.phase === "writing" ? L("Generando respuesta…", "Writing the answer…")
    : a?.phase === "tool" ? L("Pensando el siguiente paso…", "Working out the next step…") : L("Empezando…", "Starting…");
  const ctxPct = a?.ctx?.limit ? Math.min(100, (a.ctx.used / a.ctx.limit) * 100) : null;
  const hasBody = tools.length > 0 || !!reasoning || plan.length > 0 || notes.length > 0;
  const summary = [
    tools.length ? (tools.length === 1 ? L("1 paso", "1 step") : L(`${tools.length} pasos`, `${tools.length} steps`)) : null,
    failed ? L(`${failed} con error`, `${failed} failed`) : null,
    plan.length ? L(`plan ${planDone}/${plan.length}`, `plan ${planDone}/${plan.length}`) : null,
    reasoning ? L(`razonamiento ~${kTok(Math.round(reasoning.length / 4))} tok`, `reasoning ~${kTok(Math.round(reasoning.length / 4))} tok`) : null,
    warnings ? (warnings === 1 ? L("1 aviso", "1 warning") : L(`${warnings} avisos`, `${warnings} warnings`)) : null,
    total != null && total > 0.5 ? dur(total) : null,
  ].filter(Boolean).join(" · ");
  return (
    <div className={cx("my-2 overflow-hidden rounded-xl border bg-bg/60 text-xs", running ? "border-info/40" : "border-border")}>
      <button type="button" className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-elevated/50" onClick={() => hasBody && setOpen(!isOpen)} aria-expanded={isOpen}>
        {running ? <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-info" /> : <ActivityIcon className={cx("h-3.5 w-3.5 shrink-0", failed || warnings ? "text-warn" : "text-accent")} />}
        <span className="shrink-0 font-medium text-fg">{running ? L("Actividad en directo", "Live activity") : L("Actividad", "Activity")}</span>
        <span className={cx("min-w-0 truncate", running ? "text-info" : "text-muted")}>{running ? statusText : summary}</span>
        <span className="ml-auto flex shrink-0 items-center gap-2 text-[10.5px] tabular-nums text-subtle">
          {running && queued > 0 && <span className="rounded bg-accent/12 px-1.5 text-accent">{L(`${queued} en cola`, `${queued} queued`)}</span>}
          {running && a?.step != null && <span>{L("Paso", "Step")} {a.step}</span>}
          {running && plan.length > 0 && <span>{planDone}/{plan.length}</span>}
          {running && total != null && <span>{dur(total)}</span>}
          {hasBody && (isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />)}
        </span>
      </button>
      {a?.director && <div className="flex items-center gap-1.5 px-3 pb-1.5 text-[10.5px] text-subtle" title={L("El director elige qué necesita este mensaje antes de empezar", "The director picks what this message needs before it starts")}>
        <Compass className="h-3 w-3 text-accent" /><span className="truncate">{L("Director", "Director")}: {directorText(a.director, L)}{a.director.ms ? ` · ${Math.round(a.director.ms)} ms` : ""}</span></div>}
      {running && ctxPct != null && <div className="flex items-center gap-2 px-3 pb-1.5 text-[10px] text-subtle" title={L("Uso de la ventana de contexto", "Context window use")}>
        <span>{L("Contexto", "Context")}</span><span className="h-1 flex-1 overflow-hidden rounded bg-elevated"><span className={cx("block h-full transition-all", ctxPct > 85 ? "bg-warn" : "bg-info")} style={{ width: `${ctxPct}%` }} /></span>
        <span className="tabular-nums">{kTok(a!.ctx!.used)} / {kTok(a!.ctx!.limit)}</span></div>}
      {isOpen && hasBody && <>
        {plan.length > 0 && <Plan items={plan} />}
        {reasoning && <Thinking text={reasoning} live={running && a?.phase === "thinking"} />}
        {rows.length > 0 && <div className="border-t border-border/60 py-1">
          {hidden > 0 && <button type="button" className="mx-3 my-1 text-[11px] text-accent" onClick={() => setShown((s) => s + STEP_PAGE * 2)}>{L(`Ver ${Math.min(hidden, STEP_PAGE * 2)} anteriores (de ${hidden})`, `Show ${Math.min(hidden, STEP_PAGE * 2)} earlier (of ${hidden})`)}</button>}
          <ol className="space-y-px px-1">{rows.slice(hidden).map((r, i) => r.k === "t"
            ? <StepRow key={r.t.id ?? `t${r.n}`} t={r.t} n={r.n} live={running} now={running && !r.t.done ? now : 0} onOpen={onOpen} />
            : <NoteRow key={`n${hidden + i}`} n={r.n} />)}</ol>
        </div>}
      </>}
    </div>
  );
}

// `onOpen` is left out of the comparison: pages pass inline callbacks, and the panel must not re-render on every token.
export const LiveActivity = memo(LiveActivityInner, (p, n) => p.tools === n.tools && p.reasoning === n.reasoning && p.activity === n.activity
  && p.running === n.running && p.seconds === n.seconds && p.queued === n.queued);

/** Chip on a user message sent while the task was running. */
export function QueuedChip({ state }: { state?: string }) {
  const L = useL();
  if (!state) return null;
  return (
    <span className={cx("mt-1 inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10.5px]", state === "added" ? "bg-ok/12 text-ok" : state === "late" ? "bg-warn/12 text-warn" : "bg-info/12 text-info")}>
      {state === "added" ? <Check className="h-3 w-3" /> : state === "late" ? <TriangleAlert className="h-3 w-3" /> : <Loader2 className="h-3 w-3 animate-spin" />}
      {state === "added" ? L("añadido a la tarea", "added to the task") : state === "late" ? L("se responde a continuación", "answered next") : L("en cola · se añadirá a la tarea", "queued · will be added to the task")}
    </span>
  );
}

// ------------------------------------------------------------------ messages sent while a task runs

type QMsg = LiveMsg & { role: string; id?: number; content: string; pending?: boolean; queued?: string; key?: string };

/** Put a message sent while the answer runs just before that answer (the order the server stores them in). */
export function insertQueued<M extends QMsg>(x: M[], m: M): M[] {
  const last = x[x.length - 1];
  return last?.role === "assistant" && last.pending ? [...x.slice(0, -1), m, last] : [...x, m];
}

/** `user_injected`: the queued message became part of the running task (chip + a note in the timeline). */
export function markInjected<M extends QMsg>(x: M[], ev: any): M[] {
  let k = x.findIndex((m) => m.role === "user" && m.id != null && m.id === ev.message_id);
  if (k < 0) k = x.findIndex((m) => m.role === "user" && m.queued === "queued" && m.content.trim() === String(ev.content ?? "").trim());
  const y = x.map((m, i) => (i === k ? { ...m, id: m.id ?? ev.message_id, queued: "added" } : m));
  const last = y.length - 1;
  if (last >= 0 && y[last].role === "assistant") y[last] = applyLive(y[last], ev);
  return y;
}

/** Keep the chips of queued messages across a reload of the conversation (matched by message id). */
export function keepQueued<M extends QMsg>(prev: M[], next: M[], running: boolean): M[] {
  const st = new Map(prev.filter((m) => m.queued && m.id != null).map((m) => [m.id, m.queued] as const));
  if (!st.size) return next;
  return next.map((m) => { const s = m.id != null ? st.get(m.id) : undefined; return s ? { ...m, queued: running && s === "late" ? "queued" : s } : m; });
}
