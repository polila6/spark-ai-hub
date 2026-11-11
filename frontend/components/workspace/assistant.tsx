"use client";
// The workspace assistant: a chat whose turns run on the server (they continue when the page is left and
// re-attach on return) with the ws_* tools on this workspace. Messages use the shared chat actions (copy, edit and
// regenerate, reply quoting a selection, resume from a point).
import { FileCode, FileSpreadsheet, FileText, ListPlus, Loader2, MessageSquarePlus, Send, Sparkles, Square, Wrench } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { AnswerMarkdown } from "@/components/deliverable-link";
import { Badge, Button, cx } from "@/components/ui";
import { api, streamGet, streamPost } from "@/lib/api";
import { useL } from "@/lib/i18n";
import { useToast } from "@/lib/providers";
import { applyLive, insertQueued, keepQueued, LiveActivity, liveFromSaved, markInjected, QueuedChip, threadOf, toMs, type Activity } from "@/components/live-activity";
import { CodeBlock, MessageActions, MessageEditor, MSG_GROUP, quoteMarkdown, quoteStart, SelectionQuote, USER_BUBBLE_SELECTION } from "@/components/message-actions";
import { JumpToEnd, useStickToBottom } from "@/components/stick-to-bottom";
import { effortBody, EffortSlider, PermChips, useEffort } from "@/components/effort";
import { SteerToggle, useSteer } from "@/components/steer";
import { ActivityDock } from "@/components/activity-dock";
import { CONTINUE_TEXT, type Checkpoint, PauseButton, PausedNotice, RESUME_TEXT, ResumeBanner } from "@/components/resume-banner";
import { useAutoGrow, useConfirm } from "./common";
import { PanelHeader } from "./layout";

export type ChatMsg = { id?: number; role: string; content: string; tools?: any[]; pending?: boolean; reasoning?: string; activity?: Activity; metrics?: any;
  queued?: "queued" | "added" | "late"; key?: string };
const MD = { pre: CodeBlock };

/** One chat message (user bubble, or the assistant's live activity panel + markdown). */
export function WsMessage({ m, onOpen, queued }: { m: ChatMsg; onOpen: (p: string) => void; queued?: number }) {
  if (m.role === "user") return <><div className={cx("ml-8 whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-accent px-3 py-2 text-accent-fg", USER_BUBBLE_SELECTION)}>{m.content}</div><QueuedChip state={m.queued} /></>;
  const th = threadOf(m, !!m.pending);
  const body = th ? th.body : m.content;
  return (
    <div>
      <LiveActivity tools={m.tools} reasoning={m.reasoning} activity={m.activity} thread={th?.items} running={!!m.pending} queued={queued} onOpen={onOpen}
        seconds={m.metrics?.latency_ms != null ? m.metrics.latency_ms / 1000 : undefined} />
      {body && <div className="prose-chat" data-quote-scope><AnswerMarkdown content={body} streaming={!!m.pending} tools={m.tools} components={MD} onWs={onOpen} /></div>}
    </div>
  );
}

export function Assistant({ wid, onFiles, onOpen, actions }: { wid: string; onFiles: () => void; onOpen: (p: string) => void; actions?: React.ReactNode }) {
  const L = useL();
  const { push } = useToast();
  const [cid, setCid] = useState<string | null>(null);
  const [model, setModel] = useState<string>("");
  const [msgs, setMsgs] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [ckpt, setCkpt] = useState<Checkpoint | null>(null);
  const [effort, setEffort] = useEffort("sah-effort-ws");
  const [steer, setSteer] = useSteer();
  const [editing, setEditing] = useState<number | null>(null);
  const { confirm, confirmElement } = useConfirm();
  const abort = useRef<AbortController | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  useAutoGrow(box, input, 200);
  const upd = (fn: (m: ChatMsg) => ChatMsg) => setMsgs((x) => (x.length ? [...x.slice(0, -1), fn(x[x.length - 1])] : x));
  const msgsRef = useRef(msgs);
  msgsRef.current = msgs;
  const reload = useRef<() => void>(() => {});
  const onEvent = useCallback((ev: any) => {
    // the user message this turn answers: the last one sent normally (not one added to a running task)
    if (ev.type === "user_message") setMsgs((x) => { const y = [...x]; for (let j = y.length - 2; j >= 0; j--) if (y[j].role === "user" && !y[j].queued) { if (y[j].id == null) y[j] = { ...y[j], id: ev.message_id }; break; } return y; });
    else if (ev.type === "user_injected") setMsgs((x) => markInjected(x, ev));
    else if (ev.type === "delta") upd((m) => applyLive({ ...m, content: m.content + ev.content }, ev));
    else if (ev.type === "done") upd((m) => ({ ...applyLive(m, ev), pending: false, id: ev.message_id, metrics: ev.metrics, reasoning: m.reasoning || ev.reasoning || undefined }));
    else if (ev.type === "error") { upd((m) => ({ ...applyLive(m, ev), pending: false })); push("error", ev.message); }
    else { upd((m) => applyLive(m, ev)); if (ev.type === "tool_result" && ev.workspace) onFiles(); }
  }, [onFiles, push]);
  // Follow a reply: the turn runs on the server, so leaving the page never stops it; coming back re-attaches.
  const follow = useCallback(async (run: (signal: AbortSignal) => Promise<void>) => {
    abort.current?.abort();
    const ctl = new AbortController();
    abort.current = ctl;
    setBusy(true);
    try { await run(ctl.signal); } catch (e: any) { if (e.name !== "AbortError") push("error", e.message); }
    finally {
      if (abort.current === ctl) {
        setBusy(false); upd((m) => ({ ...m, pending: false })); onFiles();
        // messages sent while it worked that it never took in: the server may answer them next, so look again
        if (msgsRef.current.some((m) => m.queued === "queued")) { setMsgs((x) => x.map((m) => (m.queued === "queued" ? { ...m, queued: "late" } : m))); setTimeout(() => reload.current(), 2500); }
      }
    }
  }, [onFiles, push]);
  const load = useCallback(async (fresh = false) => {
    const r = await api(`/api/v1/workspaces/${wid}/conversation${fresh ? "?new=true" : ""}`, { method: "POST" });
    setCid(r.conversation_id); setModel(r.model_alias ?? "");
    const c = await api(`/api/v1/playground/conversations/${r.conversation_id}`);
    setCkpt(c.checkpoint ?? null);
    const history: ChatMsg[] = c.messages.map((m: any) => ({ id: m.id, role: m.role, content: m.content, metrics: m.metrics, ...(m.role === "assistant" ? liveFromSaved(m) : {}) }));
    if (c.active_turn) {
      setMsgs((prev) => keepQueued(prev, [...history, { role: "assistant", content: "", tools: [], pending: true, activity: { t0: toMs(c.active_turn.started_at) } }], true));
      follow((signal) => streamGet(`/api/v1/playground/conversations/${r.conversation_id}/stream`, (ev) => { if (ev.type !== "gone") onEvent(ev); }, signal));
    } else setMsgs((prev) => keepQueued(prev, history, false));
  }, [wid, follow, onEvent]);
  reload.current = () => { load().catch(() => {}); };
  useEffect(() => { load().catch((e) => push("error", e.message)); return () => abort.current?.abort(); }, [load, push]);
  const { away, jump } = useStickToBottom(list, msgs, msgs.length);
  async function send(text?: string) {
    const content = (text ?? input).trim();
    if (!content || !cid) return;
    if (busy) return addToTask(content);
    if (text === undefined) setInput("");
    setMsgs((x) => [...x, { role: "user", content }, { role: "assistant", content: "", tools: [], pending: true, activity: { t0: Date.now() } }]);
    await follow((signal) => streamPost(`/api/v1/playground/conversations/${cid}/messages`, { content, ...turnBody() }, onEvent, signal));
  }
  // Sent while it works: the server adds it to the running task (202 {queued}) instead of starting another answer.
  async function addToTask(content: string) {
    const key = `q${Date.now()}${Math.random()}`;
    setInput("");
    setMsgs((x) => insertQueued(x, { role: "user", content, queued: "queued", key }));
    let streamed = false;
    try {
      const r = await streamPost(`/api/v1/playground/conversations/${cid}/messages`, { content, ...turnBody(), interrupt: steer }, (ev) => {
        if (!streamed) {  // the task had just finished: the server answers it as a normal turn
          streamed = true; setBusy(true);
          setMsgs((x) => [...x.map((m) => (m.key === key ? { ...m, queued: undefined } : m)), { role: "assistant", content: "", tools: [], pending: true, activity: { t0: Date.now() } }]);
        }
        onEvent(ev);
      });
      if (r?.queued) setMsgs((x) => x.map((m) => (m.key === key ? { ...m, id: m.id ?? r.message_id } : m)));
    } catch (e: any) {
      setMsgs((x) => x.filter((m) => m.key !== key));
      setInput((v) => v || content);
      push("error", e.message);
    } finally {
      if (streamed) { setBusy(false); upd((m) => ({ ...m, pending: false })); onFiles(); }
    }
  }
  const turnBody = () => ({ model_alias: model, max_tokens: 16384, temperature: 0.3, tools: true, ...effortBody(effort), workspace_id: wid });
  // Edit a sent message (text) or answer it again: the server drops what follows and runs a new turn.
  async function regenerate(i: number, text?: string) {
    const um = msgs[i];
    if (!cid || busy || um?.role !== "user" || um.id == null) return;
    const before = msgs;
    setEditing(null);
    setMsgs([...msgs.slice(0, i), { ...um, content: text ?? um.content }, { role: "assistant", content: "", tools: [], pending: true }]);
    let started = false;
    await follow((signal) => streamPost(`/api/v1/playground/conversations/${cid}/messages/${um.id}/regenerate`, { content: text ?? null, ...turnBody() },
      (ev) => { started = true; onEvent(ev); }, signal).catch((e) => { if (!started) setMsgs(before); throw e; }));
  }
  async function resumeFrom(i: number) {
    const m = msgs[i];
    if (!cid || busy || m?.id == null) return;
    if (!(await confirm({ title: L("¿Retomar desde aquí?", "Resume from here?"), danger: true,
      text: L("Se borran los mensajes posteriores de esta conversación (los archivos del workspace no se tocan).", "Later messages of this conversation are deleted (workspace files are not touched).") }))) return;
    try {
      const r = await api(`/api/v1/playground/conversations/${cid}/messages/${m.id}/truncate`, { method: "POST" });
      setMsgs(msgs.slice(0, i + 1));
      push("ok", L(`${r.deleted} mensajes borrados: sigue desde aquí`, `${r.deleted} messages deleted: continue from here`));
      box.current?.focus();
    } catch (e: any) { push("error", e.message); }
  }
  function quote(text: string) {
    const q = quoteMarkdown(text);
    setInput((x) => (x.trim() ? `${x.trimEnd()}\n\n${q}` : q));
    requestAnimationFrame(() => { const el = box.current; if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); } });
  }
  async function stop() { if (cid) await api(`/api/v1/playground/conversations/${cid}/cancel`, { method: "POST" }).catch(() => {}); abort.current?.abort(); }
  const suggestions = [
    { icon: <FileSpreadsheet />, title: L("Datos a Excel", "Data to Excel"), text: L("Con datos.csv hazme un Excel con un resumen por mes y un gráfico", "From data.csv make an Excel with a monthly summary and a chart") },
    { icon: <FileText />, title: L("Informe PDF", "PDF report"), text: L("Genera un informe PDF de 2 páginas con las conclusiones de informe.md", "Make a 2-page PDF report from the conclusions in report.md") },
    { icon: <FileCode />, title: L("Script y prueba", "Script and test"), text: L("Crea un script que limpie los CSV de la carpeta datos/ y pruébalo", "Write a script that cleans the CSVs in data/ and test it") },
    { icon: <Wrench />, title: L("Revisar y arreglar", "Review and fix"), text: L("Revisa mi código de análisis, ejecuta los tests y arregla lo que falle", "Review my analysis code, run the tests and fix what fails") },
  ];
  const queuedCount = msgs.filter((m) => m.queued === "queued").length;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <PanelHeader icon={<Sparkles className="text-accent" />} title={L("Asistente", "Assistant")}>
        {model && <Badge className="max-w-[160px] truncate">{model}</Badge>}
        {busy && <Loader2 className="h-3.5 w-3.5 animate-spin text-info" aria-label={L("Trabajando", "Working")} />}
        <Button size="sm" variant="ghost" className="!h-7 !px-2" disabled={busy} onClick={() => load(true).catch((e) => push("error", e.message))} title={L("Nueva conversación (los archivos se quedan)", "New conversation (files stay)")}>
          <MessageSquarePlus className="h-3.5 w-3.5" /><span className="hidden text-xs sm:inline">{L("Nueva", "New")}</span>
        </Button>
        {actions}
      </PanelHeader>
      <div ref={list} className="min-h-0 flex-1 overflow-y-auto px-3 py-4 text-sm sm:px-6">
        <div className="mx-auto w-full max-w-3xl space-y-4">
          {msgs.length === 0 && (
            <div className="flex flex-col items-center gap-5 pb-4 pt-6 text-center sm:pt-12">
              <div className="rounded-2xl bg-accent/12 p-3 text-accent"><Sparkles className="h-6 w-6" /></div>
              <div className="space-y-1.5">
                <h2 className="text-base font-semibold">{L("¿Qué hacemos en este workspace?", "What shall we do in this workspace?")}</h2>
                <p className="mx-auto max-w-md text-xs text-muted">{L("Pídele trabajo sobre tus archivos: escribe el código, lo ejecuta aquí y corrige los errores él solo. Los archivos que toque aparecen como enlaces para abrirlos. Por ejemplo:", "Ask it to work on your files: it writes the code, runs it here and fixes errors on its own. Files it touches appear as links to open them. For example:")}</p>
              </div>
              <div className="grid w-full gap-2 text-left sm:grid-cols-2">
                {suggestions.map((s) => (
                  <button key={s.title} type="button" disabled={!cid} onClick={() => { setInput(s.text); box.current?.focus(); }}
                    className="group flex gap-3 rounded-xl border border-border bg-surface p-3 text-left shadow-sm transition hover:border-accent/50 hover:bg-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:opacity-60">
                    <span className="mt-0.5 shrink-0 text-accent [&>svg]:h-4 [&>svg]:w-4">{s.icon}</span>
                    <span className="min-w-0"><span className="block text-xs font-semibold">{s.title}</span><span className="mt-0.5 block text-xs text-muted">{s.text}</span></span>
                  </button>))}
              </div>
            </div>
          )}
          {msgs.map((m, i) => {
            const last = i === msgs.length - 1;
            const isUser = m.role === "user";
            if (editing === i) return <div key={i} className="flex justify-end"><MessageEditor initial={m.content} busy={busy} onCancel={() => setEditing(null)} onSave={(t) => regenerate(i, t)} /></div>;
            return (
              <div key={i} className={cx("flex flex-col", MSG_GROUP, isUser ? "items-end" : "items-stretch")}>
                <WsMessage m={m} onOpen={onOpen} queued={last && m.pending ? queuedCount : undefined} />
                {last && !isUser && !m.pending && !busy && (m as any).metrics?.paused && <PausedNotice steps={(m as any).metrics?.steps} onResume={() => send(L(CONTINUE_TEXT.es, CONTINUE_TEXT.en))} />}
                {!m.pending && <MessageActions role={m.role} text={(!isUser && threadOf(m, false)?.body.trim()) || m.content} full={m.content} busy={busy}
                  onReply={isUser ? undefined : () => quote(quoteStart(m.content))}
                  onEdit={isUser && m.id != null ? () => setEditing(i) : undefined}
                  onRegenerate={m.id == null ? undefined : isUser ? (last ? () => regenerate(i) : undefined)
                    : last && msgs[i - 1]?.role === "user" && msgs[i - 1]?.id != null ? () => regenerate(i - 1) : undefined}
                  onResume={!last && m.id != null ? () => resumeFrom(i) : undefined} />}
              </div>
            );
          })}
        </div>
      </div>
      <JumpToEnd show={away} onClick={jump} />
      <SelectionQuote root={list} onQuote={quote} />
      {confirmElement}
      {ckpt && !busy && cid && <ResumeBanner cid={cid} ckpt={ckpt} onDone={() => setCkpt(null)} onResume={async () => { await load(); await send(L(RESUME_TEXT.es, RESUME_TEXT.en)); }} />}
      <form className="shrink-0 px-3 pb-3 pt-2 sm:px-6" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <div className="mx-auto w-full max-w-3xl space-y-1.5 rounded-2xl border border-border bg-bg p-2 shadow-card transition focus-within:border-accent focus-within:ring-2 focus-within:ring-accent/25">
          <textarea ref={box} rows={2} value={input} onChange={(e) => setInput(e.target.value)} placeholder={busy ? (steer ? L("Escribe aunque esté pensando: lo lee al instante sin perder lo razonado…", "Type even while it thinks: it reads it at once without losing its reasoning…") : L("Añade indicaciones a la tarea en curso (las lee al terminar el paso)…", "Add instructions to the running task (read when the step ends)…")) : L("¿Qué hacemos en este workspace?", "What shall we do in this workspace?")}
            aria-label={L("Mensaje para el asistente", "Message for the assistant")}
            className="w-full resize-none bg-transparent px-2 py-1.5 text-sm text-fg placeholder:text-subtle focus:outline-none"
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} />
          <div className="flex flex-wrap items-center gap-2">
            <PermChips value={effort} onChange={setEffort} canThink canWeb canResearch={false} />
            <EffortSlider value={effort} onChange={setEffort} compact />
            <span className="hidden text-[10.5px] text-subtle xl:inline">{L("Mayús+Enter: nueva línea", "Shift+Enter: new line")}</span>
            {busy && <SteerToggle on={steer} onChange={setSteer} className="ml-auto" />}
            {busy && <PauseButton cid={cid} busy={busy} />}
            {busy && <Button type="button" size="sm" variant="danger" onClick={stop} title={L("Detiene la tarea", "Stops the task")}><Square className="h-3.5 w-3.5" />{L("Parar", "Stop")}</Button>}
            <Button type="submit" size="sm" variant="primary" className={busy ? undefined : "ml-auto"} disabled={!input.trim() || !cid}
              title={busy ? (steer ? L("Interrumpe su pensamiento actual (se conserva) y lo lee ya", "Interrupts its current thinking (kept) and reads it now") : L("Se suma a lo que está haciendo, sin pararlo", "Joins what it is doing, without stopping it")) : undefined}>
              {busy ? <><ListPlus className="h-3.5 w-3.5" />{steer ? L("Enviar ahora", "Send now") : L("Añadir a la tarea", "Add to the task")}</> : <><Send className="h-3.5 w-3.5" />{L("Enviar", "Send")}</>}</Button>
          </div>
        </div>
      </form>
      {(() => { const lm = msgs[msgs.length - 1]; return lm?.role === "assistant" && lm.pending ? <ActivityDock tools={lm.tools} activity={lm.activity} running onOpen={onOpen} /> : null; })()}
    </div>
  );
}
