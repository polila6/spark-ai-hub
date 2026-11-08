"use client";
import {
  Bookmark, Bot, Brain, CheckSquare, ChevronDown, Eye, Globe, Download, FileText, FolderKanban, Image as ImageIcon, ListPlus, Loader2, MessageSquarePlus, MessagesSquare,
  Paintbrush, Paperclip, Search, Send, SlidersHorizontal, Sparkles, Square, Table2, Telescope, Trash2, Volume2, Wrench, X,
} from "lucide-react";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { AgentPicker } from "@/components/agent-picker";
import { BulkBar, BulkDeleteConfirm, SelectBox, useBulk, useSelection } from "@/components/bulk";
import { AnswerMarkdown } from "@/components/deliverable-link";
import { effortBody, effortFrom, EffortSlider, PermChips, useEffort } from "@/components/effort";
import remarkGfm from "remark-gfm";
import useSWR from "swr";
import { CanvasChip, CanvasPanel } from "@/components/canvas";
import { AgentReport, ContextMeter, SkillNote, TOOL_ICONS, WebSources, CodeRun, MicButton, SpeakButton, speakText } from "@/components/assistant";
import { Guide } from "@/components/guide";
import { applyLive, insertQueued, keepQueued, LiveActivity, liveFromSaved, markInjected, QueuedChip, toMs, type Activity } from "@/components/live-activity";
import { LoadConfirm } from "@/components/memory";
import { CodeBlock, MSG_GROUP, MessageActions, MessageEditor, SelectionQuote, quoteMarkdown, quoteStart } from "@/components/message-actions";
import { Badge, Button, Card, Confirm, ErrorBox, Field, Input, Notice, Select, Textarea, Tip, cx } from "@/components/ui";
import { hubFile, useFileViewer } from "@/components/file-viewer";
import { JumpToEnd, useStickToBottom } from "@/components/stick-to-bottom";
import { useAutoGrow } from "@/components/workspace/common";
import { api, streamGet, streamPost, upload } from "@/lib/api";
import { bytes, num } from "@/lib/format";
import { useI18n, useL, tr } from "@/lib/i18n";
import { useToast } from "@/lib/providers";

type Att = { file_id: string; name: string; mime: string; kind: string; size_bytes?: number; url: string };
type ToolRun = { id: string; name: string; label?: string; args?: any; progress?: number; message?: string; status?: string; done?: boolean; ok?: boolean; summary?: string; image?: any; sources?: any[]; table?: string; memory?: any; sql?: string; remediation?: string; agent?: any; skill?: any; log?: string[]; code?: any; canvas?: any; research?: any };
type Msg = { id?: number; role: string; content: string; reasoning?: string; metrics?: any; model_alias?: string; pending?: boolean; attachments?: Att[]; tools?: ToolRun[];
  activity?: Activity; queued?: "queued" | "added" | "late"; key?: string };

/** One composer option (web, think…): a compact on/off chip instead of a labelled switch. */
function Chip({ on, onChange, icon: Icon, label, tip }: { on: boolean; onChange: (v: boolean) => void; icon: any; label: string; tip?: string }) {
  return (
    <Tip text={tip ?? label}>
      <button type="button" role="switch" aria-checked={on} aria-label={label} onClick={() => onChange(!on)}
        className={cx("inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs transition-colors", on ? "border-accent/40 bg-accent/12 font-medium text-fg" : "border-transparent text-muted hover:bg-elevated")}>
        <Icon className={cx("h-3.5 w-3.5", on && "text-accent")} /><span>{label}</span>
      </button>
    </Tip>
  );
}

function Metrics({ m }: { m: any }) {
  if (!m) return null;
  return (
    <div className="flex flex-wrap gap-1.5 text-[10px] text-subtle">
      {m.ttft_ms != null && <span>TTFT {num(m.ttft_ms, 0)} ms</span>}
      {m.tokens_per_s != null && <span>· {num(m.tokens_per_s, 1)} tok/s</span>}
      {m.completion_tokens != null && <span>· {m.completion_tokens} tok</span>}
      {m.latency_ms != null && <span>· {num(m.latency_ms / 1000, 1)} s</span>}
      {m.tools?.length > 0 && <span>· {m.tools.join(", ")}</span>}
      {m.director && <span title="Director">· 🧭 {m.director.think ? "razona" : "directo"}</span>}
    </div>
  );
}

// Generated images are shown by the tool cards; models sometimes also emit markdown images with invented URLs.
const MD_COMPONENTS = {
  img: ({ src, alt }: any) => (typeof src === "string" && src.startsWith("/api/") ? <img src={src} alt={alt ?? ""} className="max-h-80 rounded-lg" /> : null),
  pre: CodeBlock,
};

// Past-tense names of finished tools, in the page's language (read when drawn, so a language change applies at once).
const toolLabel = (n: string): string | undefined => ({
  run_code: tr("Código ejecutado", "Code run"), canvas_write: tr("Lienzo", "Canvas"), canvas_edit: tr("Lienzo editado", "Canvas edited"),
  deep_research: tr("Investigación a fondo", "Deep research"), schedule_task: tr("Tarea programada", "Task scheduled"),
  web_search: tr("Búsqueda en internet", "Web search"), fetch_url: tr("Página web", "Web page"), search_images: tr("Buscando imágenes", "Searching images"), download_file: tr("Descargando archivo", "Downloading file"), delegate_task: tr("Agente", "Agent"),
  generate_image: tr("Imagen generada", "Image generated"), edit_image: tr("Imagen editada", "Image edited"),
  search_knowledge: tr("Búsqueda en documentos", "Document search"), read_document: tr("Documento leído", "Document read"),
  query_data: tr("Consulta de datos", "Data query"), describe_data: tr("Datos analizados", "Data analysed"),
  save_memory: tr("Guardado en memoria", "Saved to memory"), use_skill: "Skill", install_skill: tr("Skill instalada", "Skill installed"),
  find_skills: tr("Búsqueda de skills", "Skills search"), compact_context: tr("Contexto compactado", "Context compacted"),
  db_schema: tr("Esquema de base de datos", "Database schema"), db_query: tr("Consulta a base de datos", "Database query"),
} as Record<string, string>)[n];

// Tools whose result is something to look at (image, table, canvas, files…) keep their card under the activity panel.
const hasOutput = (t: ToolRun) => !!(t.done && (t.image || t.table || t.canvas || t.memory || t.code?.files?.length || (t.name === "install_skill" && t.skill)));

function ToolCard({ t, onEdit, onCanvas }: { t: ToolRun; onEdit: (ref: string) => void; onCanvas: (id: string, v?: number) => void }) {
  const L = useL();
  const viewer = useFileViewer();
  const Extra = TOOL_ICONS[t.name];
  const icon = Extra ? <Extra className="h-3.5 w-3.5" /> : t.name?.includes("image") ? <ImageIcon className="h-3.5 w-3.5" /> : t.name?.includes("data") ? <Table2 className="h-3.5 w-3.5" /> : t.name === "save_memory" ? <Brain className="h-3.5 w-3.5" /> : t.name?.includes("knowledge") || t.name === "read_document" ? <FileText className="h-3.5 w-3.5" /> : <Wrench className="h-3.5 w-3.5" />;
  const webSources = (t.sources ?? []).filter((s: any) => s.url);
  const docSources = (t.sources ?? []).filter((s: any) => !s.url);
  return (
    <div className="my-2 rounded-xl border border-border bg-bg/60 text-xs">
      <div className="flex items-center gap-2 px-3 py-2">
        <span className={cx("grid h-6 w-6 place-items-center rounded-md", t.done ? (t.ok === false ? "bg-warn/15 text-warn" : "bg-accent/15 text-accent") : "bg-info/15 text-info")}>{t.done ? icon : <Loader2 className="h-3.5 w-3.5 animate-spin" />}</span>
        <span className="font-medium text-fg">{t.label || t.name}</span>
        {!t.done && t.message && <span className="truncate text-muted">· {t.message}</span>}
        {t.done && t.summary && !t.image && <span className="truncate text-muted">· {t.summary}</span>}
        {!t.done && t.name === "delegate_task" && t.args?.agent && <Badge tone="info">{t.args.agent}</Badge>}
      </div>
      {!t.done && (t.name === "delegate_task" || t.name === "deep_research") && (t.log?.length ?? 0) > 0 && <ul className="mx-3 mb-2 space-y-0.5 text-[11px] text-subtle">{t.log!.slice(-4).map((x, i) => <li key={i} className="truncate">· {x}</li>)}</ul>}
      {t.done && t.agent && <AgentReport agent={t.agent} />}
      {t.done && t.code && <CodeRun run={t.code} />}
      {t.done && t.research && <div className="border-t border-border/60 px-3 py-2"><p className="text-muted">{L("Líneas de investigación", "Research lines")}:</p><ol className="mt-1 list-decimal space-y-0.5 pl-5 text-[11px] text-subtle">{t.research.subquestions?.map((q: string, i: number) => <li key={i}>{q}</li>)}</ol></div>}
      {t.done && t.canvas && <CanvasChip canvas={t.canvas} onOpen={onCanvas} />}
      {t.done && t.skill && <SkillNote skill={t.skill} installed={t.name === "install_skill"} />}
      {t.done && webSources.length > 0 && <WebSources sources={webSources} />}
      {!t.done && t.progress != null && t.progress > 0 && <div className="mx-3 mb-2 h-1 overflow-hidden rounded bg-elevated"><div className="h-full bg-info transition-all" style={{ width: `${Math.round(t.progress * 100)}%` }} /></div>}
      {t.image && (
        <div className="px-3 pb-3">
          <button type="button" onClick={() => viewer.open({ name: `${t.image.ref || "imagen"}.png`, url: t.image.url, download: `${t.image.url}?download=true` })}><img src={t.image.url} alt={t.image.prompt} className="max-h-[420px] rounded-lg border border-border" /></button>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <Badge tone="accent">{t.image.ref}</Badge>
            {t.image.seconds && <span className="text-subtle">{num(t.image.seconds, 1)} s · {t.image.workflow}</span>}
            <a href={`${t.image.url}?download=true`}><Button size="sm" variant="ghost"><Download className="h-3.5 w-3.5" />{L("Descargar", "Download")}</Button></a>
            <Button size="sm" variant="ghost" onClick={() => onEdit(t.image.ref)}><Paintbrush className="h-3.5 w-3.5" />{L("Editar", "Edit")}</Button>
          </div>
        </div>
      )}
      {docSources.length > 0 && (
        <details className="border-t border-border/60 px-3 py-2"><summary className="cursor-pointer text-muted">{L("Fuentes", "Sources")} ({docSources.length})</summary>
          <ul className="mt-2 space-y-1.5">{docSources.map((s: any, i: number) => <li key={i}><span className="font-medium text-fg">[{i + 1}] {s.filename}{s.page ? `, p. ${s.page}` : ""}</span> <span className="text-subtle">· {s.collection}</span><p className="line-clamp-2 text-muted">{s.snippet}</p></li>)}</ul></details>
      )}
      {t.sql && <details className="border-t border-border/60 px-3 py-2"><summary className="cursor-pointer text-muted">SQL</summary><pre className="mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-[11px]">{t.sql}</pre></details>}
      {t.table && <div className="prose-chat overflow-x-auto border-t border-border/60 px-3 py-2"><ReactMarkdown remarkPlugins={[remarkGfm]}>{t.table}</ReactMarkdown></div>}
      {t.memory && <div className="flex items-center gap-2 border-t border-border/60 px-3 py-2 text-muted"><Brain className="h-3.5 w-3.5" />{t.memory.content}
        <button className="ml-auto text-accent" onClick={async () => { await api(`/api/v1/memories/${t.memory.id}`, { method: "DELETE" }); t.memory = null; }}>{L("Deshacer", "Undo")}</button></div>}
      {t.done && t.ok === false && t.remediation && <p className="border-t border-border/60 px-3 py-2 text-warn">{t.remediation}</p>}
    </div>
  );
}

function AttachmentChip({ a, onRemove }: { a: Att & { uploading?: boolean }; onRemove?: () => void }) {
  return (
    <span className="inline-flex max-w-[220px] items-center gap-1.5 rounded-lg border border-border bg-elevated px-2 py-1 text-xs">
      {a.kind === "image" && !a.uploading ? <img src={a.url} alt="" className="h-6 w-6 rounded object-cover" /> : a.uploading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : a.kind === "data" ? <Table2 className="h-3.5 w-3.5" /> : <FileText className="h-3.5 w-3.5" />}
      <span className="truncate">{a.name}</span>{a.size_bytes ? <span className="text-subtle">{bytes(a.size_bytes)}</span> : null}
      {onRemove && <button aria-label="remove" onClick={onRemove}><X className="h-3 w-3" /></button>}
    </span>
  );
}

function Chat() {
  const viewer = useFileViewer();
  const { t, lang } = useI18n();
  const L = useL();
  const { push } = useToast();
  const sp = useSearchParams();
  const { data: models } = useSWR("/api/v1/models");
  const { data: projects } = useSWR("/api/v1/projects");
  const { data: prompts, mutate: mutPrompts } = useSWR("/api/v1/playground/prompts");
  const { data: agentData } = useSWR("/api/v1/agents");
  const [projectId, setProjectId] = useState<string>(sp.get("project") ?? "");
  const { data: convs, mutate: mutConvs } = useSWR(`/api/v1/playground/conversations${projectId ? `?project_id=${projectId}` : ""}`);
  // select several chats and act on them at once (delete) instead of opening each one
  const [selecting, setSelecting] = useState(false);
  const convIds = useMemo(() => (convs?.mine ?? []).map((c: any) => c.id as string), [convs]);
  const chatSel = useSelection(convIds);
  const [delChats, setDelChats] = useState(false);
  const bulkChats = useBulk("/api/v1/playground/conversations/bulk", () => { mutConvs(); });
  const chatModels = useMemo(() => (models ?? []).filter((m: any) => m.capabilities?.includes("chat") && ["installed", "ready", "loading", "busy"].includes(m.status)), [models]);
  const [cid, setCid] = useState<string | null>(null);
  const { data: canvases, mutate: mutCanvases } = useSWR(cid ? `/api/v1/canvas?conversation_id=${cid}` : null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [model, setModel] = useState("");
  const [system, setSystem] = useState("");
  const [temp, setTemp] = useState(0.7);
  const [maxTok, setMaxTok] = useState(8192);
  const [input, setInput] = useState("");
  const [status, setStatus] = useState<string | null>(null);
  const [err, setErr] = useState<any>(null);
  const [atts, setAtts] = useState<(Att & { uploading?: boolean })[]>([]);
  const [mode, setMode] = useState<"chat" | "image">("chat");
  const [aspect, setAspect] = useState("square");
  const [tools, setTools] = useState(true);
  const [preferredAgent, setPreferredAgent] = useState("");
  const [effort, setEffort] = useEffort();
  const { data: voice } = useSWR("/api/v1/voice");
  const [autoRead, setAutoRead] = useState(false);
  const autoReadRef = useRef(false);
  autoReadRef.current = autoRead;
  const [canvas, setCanvas] = useState<{ id: string; v?: number } | null>(null);
  const [ctxLimit, setCtxLimit] = useState(0);  // 0 = the model's full window
  const [autoCompact, setAutoCompact] = useState(true);
  const [ctxUsed, setCtxUsed] = useState<number | null>(null);
  const [q, setQ] = useState("");
  const { data: found } = useSWR(q.trim().length > 1 ? `/api/v1/playground/search?q=${encodeURIComponent(q)}` : null);
  const [drag, setDrag] = useState(false);
  const abort = useRef<AbortController | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const sel = chatModels.find((m: any) => m.alias === model);
  const proj = (projects ?? []).find((p: any) => p.id === projectId);
  const availableAgents = useMemo(() => (agentData?.agents ?? []).filter((a: any) => a.enabled), [agentData]);

  // New chats use the general model (always loaded for everyone) unless the project says otherwise.
  const { data: pub } = useSWR("/api/v1/settings/public");
  const generalModel = pub?.chat_default_model || pub?.default_model;
  useEffect(() => {
    if (model || !chatModels.length || pub === undefined) return;
    const pref = proj?.default_model;
    const best = chatModels.find((m: any) => m.alias === pref) ?? chatModels.find((m: any) => m.alias === generalModel)
      ?? chatModels.find((m: any) => m.runtime) ?? chatModels.find((m: any) => m.tools_enabled) ?? chatModels[0];
    setModel(best.alias);
  }, [chatModels, model, proj, pub, generalModel]);
  const { data: active, mutate: mutActive } = useSWR("/api/v1/playground/active", { refreshInterval: 5000 });
  const activeIds = useMemo(() => new Set((active ?? []).map((a: any) => a.conversation_id)), [active]);
  const viewing = useRef<string | null>(null);  // conversation currently on screen (events of others are ignored)
  useEffect(() => () => abort.current?.abort(), []);  // leaving the page detaches; the answer keeps running

  // Tools, reasoning, plan, steps, context and notes go through applyLive (the live activity panel); the rest is the page's.
  function applyEvent(convId: string, ev: any) {
    if (viewing.current !== convId) return;
    if (ev.type === "status") { setStatus(ev.status); updLast((l) => applyLive(l, ev)); }
    // the user message this turn answers: the last one sent normally (not one added to a running task)
    else if (ev.type === "user_message") setMsgs((x) => { const y = [...x]; for (let j = y.length - 2; j >= 0; j--) if (y[j].role === "user" && !y[j].queued) { if (y[j].id == null) y[j] = { ...y[j], id: ev.message_id }; break; } return y; });
    else if (ev.type === "user_injected") setMsgs((x) => markInjected(x, ev));
    else if (ev.type === "delta") updLast((l) => applyLive({ ...l, content: l.content + ev.content }, ev));
    else if (ev.type === "context") { setCtxUsed(ev.used); updLast((l) => applyLive(l, ev)); }
    else if (ev.type === "tool_result" && ev.canvas) { setCanvas({ id: ev.canvas.id, v: ev.canvas.version }); mutCanvases(); updLast((l) => applyLive(l, ev)); }
    else if (ev.type === "done") updLast((l) => { if (autoReadRef.current && l.content) speakText(l.content, lang).catch(() => {}); return { ...applyLive(l, ev), pending: false, metrics: ev.metrics, id: ev.message_id, reasoning: l.reasoning || ev.reasoning || undefined }; });
    else if (ev.type === "error") { setErr({ message: ev.message, detail: { remediation: ev.remediation } }); updLast((l) => ({ ...applyLive(l, ev), pending: false })); }
    else updLast((l) => applyLive(l, ev));
  }

  async function follow(convId: string, run: (signal: AbortSignal) => Promise<void>) {
    abort.current?.abort();
    const ctrl = new AbortController();
    abort.current = ctrl;
    try {
      await run(ctrl.signal);
    } catch (e: any) {
      if (e.name !== "AbortError" && viewing.current === convId) setErr(e);
    } finally {
      if (viewing.current === convId && abort.current === ctrl) {
        setStatus(null);
        updLast((l) => (l?.role === "assistant" ? { ...l, pending: false } : l));
        // messages sent while it worked that it never took in: the server may answer them next, so look again
        if (msgsRef.current.some((m) => m.queued === "queued")) {
          setMsgs((x) => x.map((m) => (m.queued === "queued" ? { ...m, queued: "late" } : m)));
          setTimeout(() => { if (viewing.current === convId) openConv(convId).catch(() => {}); }, 2500);  // the server starts the follow-up turn 1.5 s after done
        }
      }
      mutConvs();
      mutActive();
    }
  }

  async function openConv(id: string) {
    abort.current?.abort();
    viewing.current = id;
    const c = await api(`/api/v1/playground/conversations/${id}`);
    setCid(id);
    setErr(null);
    setStatus(null);
    setEditing(null);
    const history: Msg[] = c.messages.map((m: any) => ({ ...m, ...(m.role === "assistant" ? liveFromSaved(m, toolLabel) : {}) }));
    if (c.model_alias) setModel(c.model_alias);
    if (c.project_id) setProjectId(c.project_id);
    setSystem(c.settings?.system_prompt ?? "");
    setPreferredAgent(c.settings?.preferred_agent ?? "");
    setCtxLimit(c.settings?.context_limit ?? 0);
    setAutoCompact(c.settings?.auto_compact ?? true);
    setEffort((cur) => effortFrom(c.settings, cur));
    setCanvas(null);
    setCtxUsed([...c.messages].reverse().find((m: any) => m.metrics?.context_used)?.metrics?.context_used ?? null);
    if (c.active_turn) {  // still answering (sent before leaving the page): replay its events and follow live
      setMsgs((prev) => keepQueued(prev, [...history, { role: "assistant", content: "", pending: true, model_alias: c.model_alias, tools: [], activity: { t0: toMs(c.active_turn.started_at) } }], true));
      setStatus("generating");
      follow(id, (signal) => streamGet(`/api/v1/playground/conversations/${id}/stream`, (ev) => {
        if (ev.type !== "gone") applyEvent(id, ev);
      }, signal));
    } else {
      setMsgs((prev) => keepQueued(prev, history, false));
    }
  }

  useEffect(() => {
    const c = sp.get("c");
    if (c) openConv(c);
    const a = sp.get("attach");  // one id, or several comma-separated (files shared from the Android app)
    if (a) Promise.all(a.split(",").filter(Boolean).map((id) => api(`/api/v1/files/${id}`).catch(() => null)))
      .then((fs) => setAtts(fs.filter(Boolean).map((f: any) => ({ file_id: f.id, name: f.filename, mime: f.mime, kind: f.kind, size_bytes: f.size_bytes, url: f.url }))));
  }, [sp]);  // eslint-disable-line react-hooks/exhaustive-deps

  async function ensureConv(): Promise<string> {
    if (cid) return cid;
    const settings = preferredAgent ? { preferred_agent: preferredAgent } : {};
    const c = await api("/api/v1/playground/conversations", { method: "POST", json: { model_alias: model || null, project_id: projectId || null, settings } });
    setCid(c.id);
    mutConvs();
    return c.id;
  }

  async function addFiles(list: FileList | File[]) {
    const arr = Array.from(list);
    if (!arr.length) return;
    const id = await ensureConv();
    for (const f of arr) {
      const tmp = { file_id: `tmp-${Math.random()}`, name: f.name || "imagen.png", mime: f.type, kind: f.type.startsWith("image/") ? "image" : "document", url: "", uploading: true };
      setAtts((x) => [...x, tmp]);
      const fd = new FormData();
      fd.append("file", f, f.name || "pegado.png");
      try {
        const r = await upload(`/api/v1/playground/conversations/${id}/attachments`, fd);
        setAtts((x) => x.map((a) => (a.file_id === tmp.file_id ? { file_id: r.id, name: r.filename, mime: r.mime, kind: r.kind, size_bytes: r.size_bytes, url: r.url } : a)));
      } catch (e: any) {
        setAtts((x) => x.filter((a) => a.file_id !== tmp.file_id));
        push("error", `${f.name}: ${e.message}`);
      }
    }
  }

  const msgsRef = useRef(msgs);
  msgsRef.current = msgs;
  function updLast(fn: (m: Msg) => Msg) {
    setMsgs((x) => { const y = [...x]; y[y.length - 1] = fn({ ...y[y.length - 1] }); return y; });
  }

  const [plan, setPlan] = useState<any>(null);
  const confirmed = useRef<string | null>(null);

  // Before loading a model that is not in memory, show what it costs (memory, idle models unloaded, ETA).
  async function needsPlan(useSel: any, m: string, then: { text: string; mode: string; run?: (alias?: string) => void }) {
    if (m !== "chat" || !useSel || useSel.runtime || confirmed.current === useSel.alias) return false;
    try {
      const pl = await api(`/api/v1/models/${useSel.id}/load-plan`);
      if (!pl.resident && (pl.evict.length || !pl.fits || (pl.expected_load_seconds ?? 0) > 45)) { setPlan({ ...pl, ...then }); return true; }
    } catch { /* the plan is advisory */ }
    return false;
  }
  const turnBody = (useModel: string, useSel: any, m: string) => ({
    model_alias: useModel, system_prompt: system || undefined, temperature: temp, max_tokens: maxTok, mode: m, tools,
    aspect, context_limit: ctxLimit || undefined, auto_compact: autoCompact,
    preferred_agent: m === "chat" ? preferredAgent || undefined : undefined,
    ...effortBody(effort),
  });
  async function choosePreferredAgent(value: string) {
    const previous = preferredAgent;
    setPreferredAgent(value);
    if (value) setTools(true);
    if (!cid) return;
    try {
      await api(`/api/v1/playground/conversations/${cid}`, { method: "PATCH", json: { preferred_agent: value || null } });
      mutConvs();
    } catch (e: any) {
      setPreferredAgent(previous);
      push("error", e.message);
    }
  }
  const pendingMsg = (alias: string): Msg => ({ role: "assistant", content: "", pending: true, model_alias: alias, tools: [], activity: { t0: Date.now() } });

  async function send(text?: string, forcedMode?: "chat" | "image", modelAlias?: string) {
    const content = (text ?? input).trim();
    const useModel = modelAlias ?? model;
    const useSel = chatModels.find((x: any) => x.alias === useModel);
    if (!content || !useModel || atts.some((a) => a.uploading)) return;
    if (status && cid) return addToTask(cid, content, useModel, useSel);
    const m = forcedMode ?? mode;
    if (await needsPlan(useSel, m, { text: content, mode: m })) return;
    setErr(null);
    try { if ("Notification" in window && Notification.permission === "default") Notification.requestPermission(); } catch { /* ignore */ }
    const id = await ensureConv();
    viewing.current = id;
    const sentAtts = atts.filter((a) => !a.uploading);
    setInput("");
    setAtts([]);
    setMsgs((x) => [...x, { role: "user", content, attachments: sentAtts }, pendingMsg(useModel)]);
    setStatus("loading");
    await follow(id, (signal) => streamPost(`/api/v1/playground/conversations/${id}/messages`, {
      content, attachments: sentAtts.map((a) => a.file_id), ...turnBody(useModel, useSel, m),
    }, (ev) => applyEvent(id, ev), signal));
  }

  // Sent while it works: the server adds it to the running task (202 {queued}) instead of starting another answer.
  async function addToTask(id: string, content: string, useModel: string, useSel: any) {
    const key = `q${Date.now()}${Math.random()}`;
    const sentAtts = atts.filter((a) => !a.uploading);
    setInput("");
    setAtts([]);
    setMsgs((x) => insertQueued(x, { role: "user", content, attachments: sentAtts, queued: "queued", key }));
    let streamed = false;
    try {
      const r = await streamPost(`/api/v1/playground/conversations/${id}/messages`, { content, attachments: sentAtts.map((a) => a.file_id), ...turnBody(useModel, useSel, mode) }, (ev) => {
        if (!streamed && viewing.current === id) {  // the task had just finished: the server answers it as a normal turn
          streamed = true;
          setStatus("generating");
          setMsgs((x) => [...x.map((m) => (m.key === key ? { ...m, queued: undefined } : m)), pendingMsg(useModel)]);
        }
        applyEvent(id, ev);
      });
      if (r?.queued) setMsgs((x) => x.map((m) => (m.key === key ? { ...m, id: m.id ?? r.message_id } : m)));
    } catch (e: any) {
      if (viewing.current === id) { setMsgs((x) => x.filter((m) => m.key !== key)); setInput((v) => v || content); setAtts((a) => (a.length ? a : sentAtts)); }
      push("error", e.message);
    } finally {
      if (streamed && viewing.current === id) { setStatus(null); updLast((l) => (l?.role === "assistant" ? { ...l, pending: false } : l)); mutConvs(); mutActive(); }
    }
  }

  // Answer the user message msgs[i] again (with `text`, it is edited first); the server deletes what came after it.
  const [editing, setEditing] = useState<number | null>(null);
  async function regenerate(i: number, text?: string, modelAlias?: string) {
    const um = msgs[i];
    const useModel = modelAlias ?? model;
    const useSel = chatModels.find((x: any) => x.alias === useModel);
    if (!cid || um?.role !== "user" || um.id == null || !useModel || status) return;
    if (await needsPlan(useSel, mode, { text: text ?? um.content, mode, run: (alias) => regenerate(i, text, alias) })) return;
    const id = cid;
    const before = msgs;
    let started = false;
    setErr(null);
    setEditing(null);
    viewing.current = id;
    setMsgs([...msgs.slice(0, i), { ...um, content: text ?? um.content }, pendingMsg(useModel)]);
    setStatus("loading");
    await follow(id, async (signal) => {
      try {
        await streamPost(`/api/v1/playground/conversations/${id}/messages/${um.id}/regenerate`, { ...turnBody(useModel, useSel, mode), content: text },
          (ev) => { started = true; applyEvent(id, ev); }, signal);
      } catch (e: any) {
        if (!started && e.name !== "AbortError" && viewing.current === id) setMsgs(before);  // refused (e.g. 409): nothing changed
        throw e;
      }
    });
  }

  // "Resume from here": keep msgs[i] and delete everything after it (asked first).
  const [rewind, setRewind] = useState<number | null>(null);
  const [rewinding, setRewinding] = useState(false);
  async function resumeFrom(i: number) {
    const m = msgs[i];
    if (!cid || m?.id == null) return;
    setRewinding(true);
    try {
      const r = await api(`/api/v1/playground/conversations/${cid}/messages/${m.id}/truncate`, { method: "POST" });
      const kept = msgs.slice(0, i + 1);
      setMsgs(kept);
      setCtxUsed([...kept].reverse().find((x) => x.metrics?.context_used)?.metrics?.context_used ?? null);
      push("ok", L(`${r.deleted} mensajes borrados: sigue desde aquí`, `${r.deleted} messages deleted: continue from here`) + (r.summary_cleared ? L(" (el resumen del contexto se rehará)", " (the context summary will be redone)") : ""));
      mutConvs();
      textRef.current?.focus();
    } catch (e: any) {
      push("error", e.message);
    } finally {
      setRewinding(false);
      setRewind(null);
    }
  }

  function quote(text: string) {
    const q = quoteMarkdown(text);
    setInput((x) => (x.trim() ? `${x.trimEnd()}\n\n${q}` : q));
    requestAnimationFrame(() => { const el = textRef.current; if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); } });
  }
  const listRef = useRef<HTMLDivElement>(null);
  const { away, jump } = useStickToBottom(listRef, msgs, msgs.length);

  async function stop() {
    if (cid) await api(`/api/v1/playground/conversations/${cid}/cancel`, { method: "POST" }).catch(() => {});
  }

  function editImage(ref: string) {
    setMode("chat");
    setInput(L(`Edita ${ref}: `, `Edit ${ref}: `));
    textRef.current?.focus();
  }

  const [drawer, setDrawer] = useState(false);
  // Chat options (system prompt, temperature, saved prompts…): closed by default so the conversation gets the width.
  const [options, setOptionsState] = useState(false);
  useEffect(() => { try { setOptionsState(localStorage.getItem("sah-chat-options") === "1"); } catch { /* storage unavailable */ } }, []);
  const setOptions = (v: boolean) => { setOptionsState(v); try { localStorage.setItem("sah-chat-options", v ? "1" : "0"); } catch { /* storage unavailable */ } };
  useAutoGrow(textRef, input, 220);
  const queuedCount = msgs.filter((m) => m.queued === "queued").length;
  const newChat = () => { abort.current?.abort(); viewing.current = null; setCanvas(null); setCid(null); setMsgs([]); setAtts([]); setErr(null); setStatus(null); setCtxUsed(null); setEditing(null); setPreferredAgent(""); };
  return (
    <div className={cx("grid h-[calc(100dvh-10.5rem)] gap-4 lg:h-[calc(100vh-7.5rem)]", canvas ? "lg:grid-cols-[230px_minmax(0,1fr)_minmax(0,1.1fr)]" : options ? "lg:grid-cols-[230px_minmax(0,1fr)] xl:grid-cols-[230px_minmax(0,1fr)_280px]" : "lg:grid-cols-[230px_minmax(0,1fr)]")}>
      <h1 className="sr-only">{L("Chat", "Chat")}</h1>
      {drawer && <div className="fixed inset-0 z-[60] bg-black/40 lg:hidden" onClick={() => setDrawer(false)} />}
      <Card className={cx("flex-col overflow-hidden lg:flex", drawer ? "fixed inset-y-0 left-0 z-[61] flex w-[85vw] max-w-sm rounded-none" : "hidden")} onClick={(e) => { if (!selecting && (e.target as HTMLElement).closest("li button")) setDrawer(false); }}>
        <div className="space-y-2 border-b border-border p-3">
          <Button variant="primary" className="w-full" onClick={newChat}><MessageSquarePlus className="h-4 w-4" />{t("play.new")}</Button>
          <div className="flex items-center gap-1.5">
            <div className="relative min-w-0 flex-1"><Search className="absolute left-2.5 top-2.5 h-3.5 w-3.5 text-subtle" /><Input className="h-8 pl-8 text-xs" placeholder={L("Buscar en tus chats…", "Search your chats…")} value={q} onChange={(e) => setQ(e.target.value)} /></div>
            <Tip text={selecting ? L("Salir de la selección", "Leave selection") : L("Seleccionar varios chats para borrarlos de una vez (también Ctrl/Cmd-clic en un chat)", "Select several chats to delete them at once (also Ctrl/Cmd-click a chat)")}>
              <Button size="icon" variant={selecting ? "outline" : "ghost"} className="h-8 w-8 shrink-0" aria-pressed={selecting} aria-label={L("Seleccionar chats", "Select chats")}
                onClick={() => { setSelecting(!selecting); chatSel.clear(); }}><CheckSquare className="h-4 w-4" /></Button>
            </Tip>
          </div>
          {(projects?.length ?? 0) > 0 && <Select className="h-8 text-xs" aria-label={L("Proyecto", "Project")} value={projectId} onChange={(e) => { setProjectId(e.target.value); setCid(null); setMsgs([]); setPreferredAgent(""); }}>
            <option value="">{L("Todos los chats (sin proyecto)", "All chats (no project)")}</option>
            {(projects ?? []).map((p: any) => <option key={p.id} value={p.id}>📁 {p.name}</option>)}
          </Select>}
        </div>
        {selecting && q.trim().length <= 1 && (
          <div className="space-y-2 border-b border-border px-3 py-2">
            <label className="flex items-center gap-2 text-xs text-muted">
              <SelectBox checked={chatSel.all} indeterminate={chatSel.some} onChange={() => chatSel.toggleAll()} label={L("Seleccionar todos los chats", "Select all chats")} />
              {chatSel.count ? L(`${chatSel.count} seleccionados`, `${chatSel.count} selected`) : L("Marca los chats (Mayús para un rango)", "Tick the chats (Shift for a range)")}
            </label>
            {chatSel.count > 0 && <BulkBar count={chatSel.count} onClear={chatSel.clear} noun={[L("chat", "chat"), L("chats", "chats")]}
              actions={[{ key: "delete", label: L("Borrar", "Delete"), icon: <Trash2 className="h-3.5 w-3.5" />, danger: true, onClick: () => setDelChats(true) }]} />}
          </div>
        )}
        <ul className="flex-1 space-y-0.5 overflow-y-auto p-2">
          {q.trim().length > 1 ? (found ?? []).map((r: any) => (
            <li key={r.id}><button className="w-full rounded-lg px-2 py-1.5 text-left hover:bg-elevated" onClick={() => openConv(r.id)}><p className="truncate text-sm">{r.title}</p><p className="line-clamp-2 text-[11px] text-muted">{r.snippet}</p></button></li>
          )) : convs?.mine?.map((c: any) => (
            <li key={c.id} className={cx("group flex items-center gap-1 rounded-lg px-2 py-1.5 text-sm", chatSel.has(c.id) ? "bg-accent/20" : cid === c.id ? "bg-accent/12" : "hover:bg-elevated")}>
              {selecting && <SelectBox checked={chatSel.has(c.id)} onChange={(_, shift) => chatSel.toggle(c.id, shift)} label={L(`Seleccionar «${c.title}»`, `Select “${c.title}”`)} />}
              <button className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-left" title={c.title}
                onClick={(e) => { if (selecting) chatSel.toggle(c.id, e.shiftKey); else if (e.ctrlKey || e.metaKey) { setSelecting(true); chatSel.toggle(c.id); } else openConv(c.id); }}>{activeIds.has(c.id) && <Loader2 className="h-3 w-3 shrink-0 animate-spin text-info" />}<span className="truncate">{c.project_id && !projectId ? "📁 " : ""}{c.title}</span></button>
              <a className={cx("opacity-0 group-hover:opacity-100", selecting && "hidden")} href={`/api/v1/playground/conversations/${c.id}/export?fmt=md`} aria-label={t("common.export")}><Download className="h-3.5 w-3.5 text-muted" /></a>
              <button className={cx("opacity-0 group-hover:opacity-100", selecting && "hidden")} aria-label={t("common.delete")} onClick={async () => { await api(`/api/v1/playground/conversations/${c.id}`, { method: "DELETE" }); if (cid === c.id) { setCid(null); setMsgs([]); } mutConvs(); }}><Trash2 className="h-3.5 w-3.5 text-muted" /></button>
            </li>
          ))}
          {q.trim().length > 1 && found && !found.length && <p className="px-2 py-4 text-center text-xs text-muted">{L("Sin resultados", "No results")}</p>}
        </ul>
        <BulkDeleteConfirm open={delChats} onOpenChange={setDelChats} busy={bulkChats.busy} noun={[L("chat", "chat"), L("chats", "chats")]}
          names={(convs?.mine ?? []).filter((c: any) => chatSel.has(c.id)).map((c: any) => c.title)}
          onConfirm={async () => {
            const ids = chatSel.list();
            const r = await bulkChats.run({ ids, action: "delete" }, (n) => L(`${n} chats borrados`, `${n} chats deleted`));
            if (r && cid && ids.includes(cid) && !r.skipped.some((s) => s.id === cid)) { setCid(null); setMsgs([]); }  // the one being read is gone
            chatSel.clear(); setDelChats(false);
            if (r && r.skipped.length === 0) setSelecting(false);
          }} />
      </Card>

      <Card className={cx("relative flex min-h-0 flex-col overflow-hidden", drag && "ring-2 ring-accent")}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); addFiles(e.dataTransfer.files); }}>
        <div className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
          <Button size="icon" variant="ghost" className="lg:hidden" aria-label={L("Conversaciones", "Conversations")} onClick={() => setDrawer(true)}><MessagesSquare className="h-5 w-5 text-accent" /></Button>
          <Button size="sm" variant="ghost" className="lg:hidden" onClick={newChat}><MessageSquarePlus className="h-4 w-4" /></Button>
          <Select aria-label={t("common.model")} className="h-8 w-auto min-w-0 max-w-[60vw] border-transparent bg-transparent text-sm font-medium hover:bg-elevated sm:max-w-[380px]" value={model} onChange={(e) => setModel(e.target.value)}>
            {chatModels.length === 0 && <option value="">{t("play.select_model")}</option>}
            {chatModels.map((m: any) => <option key={m.id} value={m.alias}>{m.alias === generalModel ? "★ " : ""}{m.alias}{m.capabilities.includes("vision") ? " 👁" : ""}{m.tools_enabled ? " 🛠" : ""}{m.runtime ? ` · ${L("en memoria", "loaded")}` : ` · ${L("hay que cargarlo", "needs loading")}`}</option>)}
          </Select>
          {sel && <div className="flex items-center gap-1.5 text-muted">
            {sel.capabilities.includes("vision") && <Tip text={L("Puede ver imágenes", "Can see images")}><span tabIndex={0} aria-label="vision"><Eye className="h-3.5 w-3.5" /></span></Tip>}
            {sel.tools_enabled && <Tip text={L("Puede usar herramientas", "Can use tools")}><span tabIndex={0} aria-label="tools"><Wrench className="h-3.5 w-3.5" /></span></Tip>}
            <Tip text={sel.runtime ? L("En memoria: responde al momento", "Loaded: answers right away") : L("Se cargará al enviar: la primera respuesta tarda", "Loads on send: the first answer takes longer")}><span tabIndex={0} aria-label={sel.runtime ? L("en memoria", "loaded") : L("se cargará al enviar", "loads on send")} className={cx("h-2 w-2 rounded-full", sel.runtime ? "bg-ok" : "bg-subtle")} /></Tip>
          </div>}
          {proj && <Badge tone="info"><FolderKanban className="h-3 w-3" />{proj.name}</Badge>}
          {sel && <ContextMeter cid={cid} modelAlias={sel.alias} windowMax={sel.context_window} used={ctxUsed} limit={ctxLimit} setLimit={setCtxLimit} autoCompact={autoCompact} setAutoCompact={setAutoCompact} busy={!!status} />}
          {!canvas && (canvases?.length ?? 0) > 0 && <Button size="sm" variant="outline" onClick={() => setCanvas({ id: canvases[0].id, v: canvases[0].version })}><FileText className="h-3.5 w-3.5" />{L("Lienzo", "Canvas")}{canvases.length > 1 ? ` (${canvases.length})` : ""}</Button>}
          <div className="ml-auto flex items-center gap-1">
            <Tip text={L("Opciones: instrucciones, temperatura, longitud, prompts guardados y atajos", "Options: instructions, temperature, length, saved prompts and shortcuts")}><Button size="icon" variant={options ? "outline" : "ghost"} aria-label={L("Opciones del chat", "Chat options")} aria-expanded={options} onClick={() => setOptions(!options)}><SlidersHorizontal className="h-4 w-4" /></Button></Tip>
          </div>
        </div>
        <div ref={listRef} className="flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-3xl space-y-7 px-4 py-6 sm:px-6">
          {msgs.length === 0 && (
            <div className="pt-[6vh]">
              <p className="mb-6 text-center text-2xl font-semibold tracking-tight">{L("¿En qué trabajamos hoy?", "What are we working on today?")}</p>
              <div className="grid gap-2 sm:grid-cols-2">
                {[[L("Resume este PDF en 5 puntos", "Summarise this PDF in 5 points"), FileText], [L("Genera una ilustración de un taller de diseño con luz cálida", "Generate an illustration of a design workshop with warm light"), ImageIcon],
                  [L("¿Qué dicen mis documentos sobre …?", "What do my documents say about …?"), Search], [L("Con este Excel, calcula las ventas por mes y haz una tabla", "With this Excel, compute sales per month as a table"), Table2],
                  [L("Busca en internet las últimas novedades sobre impresión 3D en metal y cita las fuentes", "Search the web for the latest news on metal 3D printing and cite sources"), Globe], [L("Lanza dos agentes en paralelo: uno que busque artículos científicos sobre … y otro que revise mi borrador", "Run two agents in parallel: one finding papers on … and one reviewing my draft"), Bot]].map(([s, I]: any) => (
                  <button key={s} className="flex items-start gap-2.5 rounded-xl border border-border p-3 text-left text-[13px] leading-snug text-muted transition-colors hover:bg-elevated hover:text-fg" onClick={() => { setInput(s); textRef.current?.focus(); }}><I className="mt-0.5 h-4 w-4 shrink-0 text-accent" />{s}</button>))}
              </div>
              <Guide id="chat" className="mb-0 mt-6" />
            </div>
          )}
          {msgs.map((m, i) => {
            const last = i === msgs.length - 1;
            const prevUser = m.role === "assistant" ? msgs.slice(0, i).map((x, j) => [x, j] as const).reverse().find(([x]) => x.role === "user")?.[1] : undefined;
            const canRegen = !m.pending && last && cid && (m.role === "user" ? m.id != null : prevUser != null && msgs[prevUser].id != null);
            return (
            <div key={i} className={cx("flex flex-col", MSG_GROUP, m.role === "user" ? "items-end" : "items-start")}>
              {editing === i ? <MessageEditor initial={m.content} busy={!!status} onCancel={() => setEditing(null)} onSave={(txt) => regenerate(i, txt)} /> : (
              <div className={cx("text-[14.5px]", m.role === "user" ? "max-w-[85%] rounded-2xl rounded-br-md border border-accent/20 bg-accent/10 px-4 py-2.5 leading-relaxed" : "w-full")}>
                {m.role === "user" ? (
                  <>
                    {m.attachments && m.attachments.length > 0 && <div className="mb-2 flex flex-wrap gap-1.5">{m.attachments.map((a) => a.kind === "image" ? <button key={a.file_id} type="button" onClick={() => viewer.open(hubFile(a), m.attachments!.map(hubFile))}><img src={a.url} alt={a.name} className="max-h-40 rounded-lg" /></button> : <button key={a.file_id} type="button" title={L("Ver", "View")} className="inline-flex items-center gap-1 rounded-md bg-bg/70 px-2 py-0.5 text-xs hover:bg-bg" onClick={() => viewer.open(hubFile(a), m.attachments!.map(hubFile))}><FileText className="h-3 w-3" />{a.name}</button>)}</div>}
                    <p className="whitespace-pre-wrap break-words">{m.content}</p>
                  </>
                ) : (
                  <>
                    <LiveActivity tools={m.tools} reasoning={m.reasoning} activity={m.activity} running={!!m.pending} queued={last && m.pending ? queuedCount : undefined}
                      seconds={m.metrics?.latency_ms != null ? m.metrics.latency_ms / 1000 : undefined} />
                    {m.tools?.filter(hasOutput).map((tr) => <ToolCard key={tr.id} t={tr} onEdit={editImage} onCanvas={(id, v) => setCanvas({ id, v })} />)}
                    <div className="prose-chat" data-quote-scope>{m.content && <AnswerMarkdown content={m.content} streaming={!!m.pending} tools={m.tools} components={MD_COMPONENTS} onCanvas={(id, v) => setCanvas({ id, v })} />}</div>
                    {!m.pending && <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 transition-opacity [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:focus-within:opacity-100 [@media(hover:hover)]:group-hover/msg:opacity-100">
                      {m.model_alias && <span className="text-[10px] text-subtle">{m.model_alias}</span>}<Metrics m={m.metrics} />{voice?.enabled && voice?.read_aloud && m.content && <SpeakButton text={m.content} lang={lang} />}</div>}
                  </>
                )}
              </div>)}
              {m.role === "user" && editing !== i && <QueuedChip state={m.queued} />}
              {editing !== i && !m.pending && <MessageActions role={m.role} text={m.content} busy={!!status}
                onReply={m.role === "assistant" ? () => quote(quoteStart(m.content)) : undefined}
                onEdit={m.role === "user" && m.id != null && cid ? () => setEditing(i) : undefined}
                onRegenerate={canRegen ? () => regenerate(m.role === "user" ? i : prevUser!) : undefined}
                onResume={!last && m.id != null && cid ? () => setRewind(i) : undefined} />}
            </div>
            );
          })}
          {err && <ErrorBox error={err} />}
          </div>
        </div>
        <JumpToEnd show={away} onClick={jump} />
        <form className="px-3 pb-3 pt-1 sm:px-6" onSubmit={(e) => { e.preventDefault(); send(); }}>
          <div className="mx-auto max-w-3xl rounded-2xl border border-border bg-surface shadow-sm transition-colors focus-within:border-accent/60">
          {atts.length > 0 && <div className="flex flex-wrap gap-1.5 px-3 pt-3">{atts.map((a) => <AttachmentChip key={a.file_id} a={a} onRemove={() => setAtts((x) => x.filter((y) => y.file_id !== a.file_id))} />)}</div>}
            <input ref={fileInput} type="file" multiple className="hidden" onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = ""; }} />
            <textarea ref={textRef} aria-label={t("play.placeholder")} rows={1} className="block w-full resize-none bg-transparent px-4 pb-1 pt-3 text-[14.5px] leading-relaxed text-fg placeholder:text-subtle focus:outline-none" value={input} onChange={(e) => setInput(e.target.value)}
              placeholder={status ? L("Añade indicaciones a la tarea en curso (no la interrumpe)…", "Add instructions to the running task (it is not interrupted)…") : mode === "image" ? L("Describe la imagen que quieres (o qué cambiar en la adjunta)…", "Describe the image you want (or what to change in the attached one)…") : L("Escribe tu mensaje…", "Type your message…")}
              onPaste={(e) => { const fs = Array.from(e.clipboardData.files); if (fs.length) { e.preventDefault(); addFiles(fs); } }}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} />
          <div className="flex items-end gap-2 px-2 pb-2">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1">
              <Tip text={L("Adjuntar archivos o imágenes (también puedes arrastrar o pegar)", "Attach files or images (you can also drag or paste)")}><Button type="button" size="icon" variant="ghost" onClick={() => fileInput.current?.click()}><Paperclip className="h-4 w-4" /></Button></Tip>
              <Tip text={mode === "image" ? L("Modo imagen activo: tu mensaje es el prompt (con imagen adjunta, la edita)", "Image mode on: your message is the prompt (with an attached image, it edits it)") : L("Activar modo imagen", "Switch to image mode")}>
                <Button type="button" size="icon" variant={mode === "image" ? "primary" : "ghost"} onClick={() => setMode(mode === "image" ? "chat" : "image")}><Paintbrush className="h-4 w-4" /></Button></Tip>
              {voice?.enabled && voice?.dictation && <MicButton lang={lang} onText={(txt) => { setInput((x) => (x ? `${x} ${txt}` : txt)); textRef.current?.focus(); }} />}
            {mode === "image" ? (
              <label className="flex items-center gap-2 px-1 text-xs text-muted">{L("Formato", "Format")}<Select className="h-7 w-auto text-xs" value={aspect} onChange={(e) => setAspect(e.target.value)}>
                <option value="square">{L("Cuadrado", "Square")}</option><option value="landscape">{L("Horizontal 4:3", "Landscape 4:3")}</option><option value="portrait">{L("Vertical 3:4", "Portrait 3:4")}</option><option value="wide">16:9</option><option value="tall">9:16</option></Select></label>
            ) : (
              <>
                {agentData?.settings?.enabled !== false && availableAgents.length > 0 && (
                  <AgentPicker value={preferredAgent} agents={availableAgents} disabled={!!status} onChange={choosePreferredAgent} />
                )}
                <Chip on={tools} onChange={setTools} icon={Wrench} label={L("Herramientas", "Tools")} tip={L("Archivos, código, imágenes, agentes y skills", "Files, code, images, agents and skills")} />
                <PermChips value={effort} onChange={setEffort} canThink={sel?.backend === "vllm"} canWeb={!!(tools && sel?.tools_enabled && pub?.web_search_enabled !== false)}
                  canResearch={!!(tools && sel?.tools_enabled && pub?.web_search_enabled !== false && pub?.agents_enabled !== false)} />
                <EffortSlider value={effort} onChange={setEffort} compact />
                {voice?.enabled && voice?.read_aloud && <Chip on={autoRead} onChange={setAutoRead} icon={Volume2} label={L("Leer", "Read")} tip={L("Leer las respuestas en voz alta", "Read answers aloud")} />}
                {sel && !sel.tools_enabled && <span className="px-1 text-xs text-warn">{L("Este modelo no usa herramientas: para imágenes usa el modo 🎨.", "This model has no tools: use 🎨 mode for images.")}</span>}
              </>
            )}
            </div>
            <div className="flex shrink-0 items-center gap-1.5">
            {status ? <>
              <Button key="add" type="submit" size="sm" variant="primary" disabled={!model || !input.trim() || atts.some((a) => a.uploading)} title={L("Se suma a lo que está haciendo, sin pararlo", "Joins what it is doing, without stopping it")}><ListPlus className="h-4 w-4" /><span className="hidden sm:inline">{L("Añadir a la tarea", "Add to the task")}</span></Button>
              <Button key="stop" type="button" size="sm" variant="danger" onClick={stop}><Square className="h-3.5 w-3.5" /><span className="hidden sm:inline">{t("play.stop")}</span></Button></>
              : <Button key="send" type="submit" size="sm" variant="primary" disabled={!model || !input.trim() || atts.some((a) => a.uploading)}>{mode === "image" ? <Sparkles className="h-4 w-4" /> : <Send className="h-4 w-4" />}{mode === "image" ? L("Crear", "Create") : t("play.send")}</Button>}
            </div>
          </div>
          </div>
        </form>
        {drag && <div className="pointer-events-none absolute inset-0 grid place-items-center bg-accent/10 text-sm font-medium text-accent">{L("Suelta para adjuntar", "Drop to attach")}</div>}
      </Card>

      {canvas && <Card className="fixed inset-0 z-[62] flex min-h-0 flex-col overflow-hidden rounded-none lg:static lg:z-auto lg:rounded-2xl">
        <CanvasPanel id={canvas.id} version={canvas.v} onClose={() => setCanvas(null)} busy={!!status} onAsk={(text) => { send(text); }} />
      </Card>}
      {options && <div className={cx("fixed inset-0 z-[60] bg-black/30", !canvas && "xl:hidden")} onClick={() => setOptions(false)} />}
      <Card className={cx("overflow-y-auto p-4", !options ? "hidden" : cx("fixed inset-y-0 right-0 z-[61] block w-[min(340px,92vw)] rounded-none", !canvas && "xl:static xl:z-auto xl:w-auto xl:rounded-2xl"))}>
        <div className="space-y-4">
          <div className="flex items-center justify-between"><span className="text-sm font-semibold">{L("Opciones del chat", "Chat options")}</span><Button size="icon" variant="ghost" aria-label={t("common.close")} onClick={() => setOptions(false)}><X className="h-4 w-4" /></Button></div>
          <Field label={t("play.system")} hint={L("Se suma a tus instrucciones personales y a las del proyecto", "Added to your personal and project instructions")}><Textarea rows={5} value={system} onChange={(e) => setSystem(e.target.value)} /></Field>
          <Field label={`${t("play.temperature")}: ${temp}`}><input aria-label={t("play.temperature")} type="range" min={0} max={2} step={0.05} value={temp} onChange={(e) => setTemp(Number(e.target.value))} className="w-full accent-[rgb(var(--accent))]" /></Field>
          <Field label={t("play.max_tokens")}><Input type="number" min={1} max={32768} value={maxTok} onChange={(e) => setMaxTok(Number(e.target.value))} /></Field>
          <Notice tone="muted">{L("Tus conversaciones son privadas y se guardan en el Spark. El asistente usa tu Memoria y tus instrucciones (página Memoria).", "Your conversations are private and stored on the Spark. The assistant uses your Memory and instructions (Memory page).")}</Notice>
          <div>
            <div className="mb-2 flex items-center justify-between"><span className="text-xs font-medium text-muted">{t("play.prompts")}</span>
              <Button size="sm" variant="ghost" disabled={!input && !system} onClick={async () => { await api("/api/v1/playground/prompts", { method: "POST", json: { title: (input || system).slice(0, 40), content: input, system_prompt: system || null } }); mutPrompts(); push("ok", t("common.save")); }}><Bookmark className="h-3.5 w-3.5" />{t("play.save_prompt")}</Button></div>
            <ul className="space-y-1">
              {prompts?.map((p: any) => (
                <li key={p.id} className="group flex items-center gap-1 rounded-lg px-2 py-1 text-xs hover:bg-elevated">
                  <button className="flex-1 truncate text-left" onClick={() => { setInput(p.content); if (p.system_prompt) setSystem(p.system_prompt); }}>{p.title}</button>
                  <button className="opacity-0 group-hover:opacity-100" aria-label={t("common.delete")} onClick={async () => { await api(`/api/v1/playground/prompts/${p.id}`, { method: "DELETE" }); mutPrompts(); }}><Trash2 className="h-3 w-3" /></button>
                </li>
              ))}
            </ul>
          </div>
          <details className="text-xs text-muted"><summary className="cursor-pointer"><ChevronDown className="mr-1 inline h-3 w-3" />{L("Atajos", "Shortcuts")}</summary>
            <ul className="mt-2 space-y-1"><li>Enter — {L("enviar", "send")}</li><li>Shift+Enter — {L("nueva línea", "new line")}</li><li>Ctrl+V — {L("pegar imagen", "paste image")}</li><li>«{L("recuerda que…", "remember that…")}» — {L("guardar en memoria", "save to memory")}</li><li>«{L("edita img2: …", "edit img2: …")}» — {L("editar una imagen", "edit an image")}</li></ul></details>
        </div>
      </Card>
      {plan && <LoadConfirm plan={plan} defaultName={generalModel && generalModel !== plan.model ? generalModel : undefined}
        onCancel={() => setPlan(null)}
        onUseDefault={() => { const pl = plan; setPlan(null); setModel(generalModel); if (pl.run) pl.run(generalModel); else send(pl.text, pl.mode, generalModel); }}
        onConfirm={() => { const pl = plan; confirmed.current = pl.model; setPlan(null); if (pl.run) pl.run(); else send(pl.text, pl.mode); }} />}
      <SelectionQuote root={listRef} onQuote={quote} />
      <Confirm open={rewind != null} onOpenChange={(o) => { if (!o && !rewinding) setRewind(null); }} danger busy={rewinding} title={L("Retomar desde aquí", "Resume from here")}
        text={rewind == null ? "" : L(`Se borrarán ${msgs.length - 1 - rewind} mensajes posteriores a este (no se puede deshacer). ${msgs[rewind]?.role === "user" ? "Después pulsa «Regenerar» en este mensaje o escribe otro." : "Después escribe tu siguiente mensaje."}`,
          `The ${msgs.length - 1 - rewind} messages after this one will be deleted (cannot be undone). ${msgs[rewind]?.role === "user" ? "Then click “Regenerate” on this message or type another one." : "Then type your next message."}`)}
        onConfirm={() => { if (rewind != null) resumeFrom(rewind); }} />
    </div>
  );
}

export default function Playground() {
  return <Suspense><Chat /></Suspense>;
}
