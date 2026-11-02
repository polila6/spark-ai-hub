"use client";
// The workspace assistant: a chat whose turns run on the server (they continue when the page is left and
// re-attach on return) with the ws_* tools on this workspace. Messages use the shared chat actions (copy, edit and
// regenerate, reply quoting a selection, resume from a point).
import { Loader2, MessageSquarePlus, Send, Sparkles, Square, Wrench } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Badge, Button, cx } from "@/components/ui";
import { api, streamGet, streamPost } from "@/lib/api";
import { useL } from "@/lib/i18n";
import { useToast } from "@/lib/providers";
import { CodeBlock, MessageActions, MessageEditor, MSG_GROUP, quoteMarkdown, quoteStart, SelectionQuote, USER_BUBBLE_SELECTION } from "@/components/message-actions";
import { useAutoGrow, useConfirm } from "./common";

export type ChatMsg = { id?: number; role: string; content: string; tools?: any[]; pending?: boolean };
const MD = { pre: CodeBlock };

export function ToolLine({ t, onOpen }: { t: any; onOpen: (p: string) => void }) {
  const L = useL();
  const changed: string[] = t.workspace?.changed ?? [];
  return (
    <div className="my-1.5 rounded-lg border border-border bg-bg/60 px-2.5 py-1.5 text-[11.5px]">
      <div className="flex items-center gap-1.5">{t.done ? <Wrench className={cx("h-3 w-3 shrink-0", t.ok === false ? "text-warn" : "text-accent")} /> : <Loader2 className="h-3 w-3 shrink-0 animate-spin text-info" />}
        <span className="font-medium">{t.label || t.name}</span>{(t.summary || t.message) && <span className="truncate text-muted">· {t.done ? t.summary : t.message}</span>}</div>
      {t.terminal && <details className="mt-1"><summary className="cursor-pointer truncate font-mono text-[11px] text-muted">$ {t.terminal.command.split("\n")[0].slice(0, 140)}{t.terminal.command.includes("\n") ? " …" : ""}</summary>
        <pre className="mt-1 max-h-48 overflow-auto whitespace-pre-wrap rounded bg-[#0d1117] p-2 font-mono text-[11px] text-[#c9d1d9]">{t.terminal.stdout}{t.terminal.stderr ? `\n${t.terminal.stderr}` : ""}</pre></details>}
      {t.agent?.report && <details className="mt-1"><summary className="cursor-pointer text-muted">{L(`Informe de ${t.agent.name} · ${t.agent.steps?.length ?? 0} pasos`, `Report from ${t.agent.name} · ${t.agent.steps?.length ?? 0} steps`)}</summary>
        <div className="prose-chat mt-1 max-h-72 overflow-y-auto text-[12px]"><ReactMarkdown remarkPlugins={[remarkGfm]} components={MD}>{t.agent.report}</ReactMarkdown></div></details>}
      {changed.length > 0 && <div className="mt-1 flex flex-wrap gap-1">{changed.slice(0, 10).map((p) => <button key={p} type="button" className="rounded border border-border px-1.5 font-mono text-[10.5px] hover:bg-elevated" onClick={() => onOpen(p)}>{p}</button>)}</div>}
      {t.done && t.ok === false && t.remediation && <p className="mt-1 text-warn">{t.remediation}</p>}
      {!t.done && t.name === "ws_run" && <p className="mt-0.5 text-[10.5px] text-subtle">{L("Se ejecuta en el Spark, aislado", "Runs on the Spark, isolated")}</p>}
    </div>
  );
}

/** One chat message (user bubble, or the assistant's tools + markdown). */
export function WsMessage({ m, onOpen }: { m: ChatMsg; onOpen: (p: string) => void }) {
  if (m.role === "user") return <div className={cx("ml-8 whitespace-pre-wrap break-words rounded-2xl rounded-br-md bg-accent px-3 py-2 text-accent-fg", USER_BUBBLE_SELECTION)}>{m.content}</div>;
  return (
    <div>
      {m.tools?.map((t, j) => <ToolLine key={t.id ?? j} t={t} onOpen={onOpen} />)}
      {m.content ? <div className="prose-chat" data-quote-scope><ReactMarkdown remarkPlugins={[remarkGfm]} components={MD}>{m.content}</ReactMarkdown></div>
        : m.pending && !(m.tools?.length) && <Loader2 className="h-4 w-4 animate-spin text-muted" />}
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
  const [think, setThink] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const { confirm, confirmElement } = useConfirm();
  const abort = useRef<AbortController | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const box = useRef<HTMLTextAreaElement>(null);
  useAutoGrow(box, input, 200);
  const upd = (fn: (m: ChatMsg) => ChatMsg) => setMsgs((x) => (x.length ? [...x.slice(0, -1), fn(x[x.length - 1])] : x));
  const onEvent = useCallback((ev: any) => {
    if (ev.type === "user_message") setMsgs((x) => { const y = [...x]; const j = y.length - 2; if (y[j]?.role === "user") y[j] = { ...y[j], id: ev.message_id }; return y; });
    else if (ev.type === "delta") upd((m) => ({ ...m, content: m.content + ev.content }));
    else if (ev.type === "tool_start") upd((m) => ({ ...m, tools: [...(m.tools ?? []), { id: ev.id, name: ev.name, label: ev.label, args: ev.args }] }));
    else if (ev.type === "tool_progress") upd((m) => ({ ...m, tools: (m.tools ?? []).map((t) => (t.id === ev.id ? { ...t, message: ev.message } : t)) }));
    else if (ev.type === "tool_result") { upd((m) => ({ ...m, tools: (m.tools ?? []).map((t) => (t.id === ev.id ? { ...t, ...ev, done: true } : t)) })); if (ev.workspace) onFiles(); }
    else if (ev.type === "done") upd((m) => ({ ...m, pending: false, id: ev.message_id }));
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
    const history: ChatMsg[] = c.messages.map((m: any) => ({ id: m.id, role: m.role, content: m.content, tools: (m.parts ?? []).filter((p: any) => p.type === "tool").map((p: any) => ({ ...p, done: true })) }));
    if (c.active_turn) {
      setMsgs([...history, { role: "assistant", content: "", tools: [], pending: true }]);
      follow((signal) => streamGet(`/api/v1/playground/conversations/${r.conversation_id}/stream`, (ev) => { if (ev.type !== "gone") onEvent(ev); }, signal));
    } else setMsgs(history);
  }, [wid, follow, onEvent]);
  useEffect(() => { load().catch((e) => push("error", e.message)); return () => abort.current?.abort(); }, [load, push]);
  useEffect(() => { const el = list.current; if (el) el.scrollTop = el.scrollHeight; }, [msgs]);  // not scrollIntoView: it would scroll the page too
  async function send() {
    const content = input.trim();
    if (!content || !cid || busy) return;
    setInput("");
    setMsgs((x) => [...x, { role: "user", content }, { role: "assistant", content: "", tools: [], pending: true }]);
    await follow((signal) => streamPost(`/api/v1/playground/conversations/${cid}/messages`, { content, ...turnBody() }, onEvent, signal));
  }
  const turnBody = () => ({ model_alias: model, max_tokens: 16384, temperature: 0.3, tools: true, thinking: think, web: true, workspace_id: wid });
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
    L("Con datos.csv hazme un Excel con un resumen por mes y un gráfico", "From data.csv make an Excel with a monthly summary and a chart"),
    L("Genera un informe PDF de 2 páginas con las conclusiones de informe.md", "Make a 2-page PDF report from the conclusions in report.md"),
    L("Crea un script que limpie los CSV de la carpeta datos/ y pruébalo", "Write a script that cleans the CSVs in data/ and test it"),
    L("Revisa mi código de análisis, ejecuta los tests y arregla lo que falle", "Review my analysis code, run the tests and fix what fails"),
  ];
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border px-3">
        <Sparkles className="h-3.5 w-3.5 text-accent" />
        <span className="text-xs font-semibold">{L("Asistente", "Assistant")}</span>{model && <Badge className="max-w-[140px] truncate">{model}</Badge>}
        {busy && <Loader2 className="h-3.5 w-3.5 animate-spin text-info" />}
        <div className="ml-auto flex items-center gap-0.5">
          <Button size="sm" variant="ghost" className="!h-7 !px-2" disabled={busy} onClick={() => load(true).catch((e) => push("error", e.message))} title={L("Nueva conversación (los archivos se quedan)", "New conversation (files stay)")}><MessageSquarePlus className="h-3.5 w-3.5" /></Button>
          {actions}
        </div>
      </div>
      <div ref={list} className="min-h-0 flex-1 space-y-3 overflow-y-auto p-3 text-sm">
        {msgs.length === 0 && <div className="space-y-2 text-xs text-muted">
          <p>{L("Pídele trabajo sobre tus archivos: escribe el código, lo ejecuta aquí y corrige los errores él solo. Los archivos que toque aparecen como enlaces para abrirlos. Por ejemplo:", "Ask it to work on your files: it writes the code, runs it here and fixes errors on its own. Files it touches appear as links to open them. For example:")}</p>
          {suggestions.map((s) => <button key={s} type="button" className="block w-full rounded-lg border border-border p-2 text-left hover:bg-elevated" onClick={() => { setInput(s); box.current?.focus(); }}>{s}</button>)}
        </div>}
        {msgs.map((m, i) => {
          const last = i === msgs.length - 1;
          const isUser = m.role === "user";
          if (editing === i) return <div key={i} className="flex justify-end"><MessageEditor initial={m.content} busy={busy} onCancel={() => setEditing(null)} onSave={(t) => regenerate(i, t)} /></div>;
          return (
            <div key={i} className={cx("flex flex-col", MSG_GROUP, isUser ? "items-end" : "items-stretch")}>
              <WsMessage m={m} onOpen={onOpen} />
              {!m.pending && <MessageActions role={m.role} text={m.content} busy={busy}
                onReply={isUser ? undefined : () => quote(quoteStart(m.content))}
                onEdit={isUser && m.id != null ? () => setEditing(i) : undefined}
                onRegenerate={m.id == null ? undefined : isUser ? (last ? () => regenerate(i) : undefined)
                  : last && msgs[i - 1]?.role === "user" && msgs[i - 1]?.id != null ? () => regenerate(i - 1) : undefined}
                onResume={!last && m.id != null ? () => resumeFrom(i) : undefined} />}
            </div>
          );
        })}
      </div>
      <SelectionQuote root={list} onQuote={quote} />
      {confirmElement}
      <form className="shrink-0 space-y-1.5 border-t border-border p-2" onSubmit={(e) => { e.preventDefault(); send(); }}>
        <textarea ref={box} rows={2} value={input} onChange={(e) => setInput(e.target.value)} placeholder={L("¿Qué hacemos en este workspace?", "What shall we do in this workspace?")}
          aria-label={L("Mensaje para el asistente", "Message for the assistant")}
          className="w-full resize-none rounded-lg border border-border bg-bg px-3 py-2 text-sm text-fg placeholder:text-subtle transition focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25"
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} />
        <div className="flex items-center gap-2">
          <label className="flex min-w-0 items-center gap-1.5 text-[11px] text-muted" title={L("Más lento, mejor en problemas difíciles", "Slower, better for hard problems")}>
            <input type="checkbox" checked={think} onChange={(e) => setThink(e.target.checked)} />{L("Pensar más", "Think harder")}</label>
          <span className="hidden text-[10.5px] text-subtle xl:inline">{L("Mayús+Enter: nueva línea", "Shift+Enter: new line")}</span>
          {busy ? <Button type="button" size="sm" variant="danger" className="ml-auto" onClick={stop}><Square className="h-3.5 w-3.5" />{L("Parar", "Stop")}</Button>
            : <Button type="submit" size="sm" variant="primary" className="ml-auto" disabled={!input.trim() || !cid}><Send className="h-3.5 w-3.5" />{L("Enviar", "Send")}</Button>}
        </div>
      </form>
    </div>
  );
}
