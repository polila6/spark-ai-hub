"use client";
// Chat extensions: context-window meter/settings, and the cards for web sources, sub-agents and skills.
import { Bot, Code2, Download, ExternalLink, FileText, Gauge, Globe, Loader2, Mic, Puzzle, Shrink, Square, Volume2 } from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR from "swr";
import { api, postBlob, upload } from "@/lib/api";
import { useL } from "@/lib/i18n";
import { useToast } from "@/lib/providers";
import { Badge, Button, Field, Modal, Notice, Select, Switch, cx } from "./ui";

export const kTok = (n?: number | null) => (n == null ? "—" : n >= 1024 && n % 1024 === 0 ? `${n / 1024}k` : n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n));
const SIZES = [4096, 8192, 16384, 32768, 65536, 131072, 262144];

export function ContextMeter({ cid, modelAlias, windowMax, used, limit, setLimit, autoCompact, setAutoCompact, onChanged, busy }: {
  cid: string | null; modelAlias: string; windowMax?: number; used?: number | null; limit: number; setLimit: (n: number) => void;
  autoCompact: boolean; setAutoCompact: (b: boolean) => void; onChanged?: () => void; busy?: boolean;
}) {
  const L = useL();
  const { push } = useToast();
  const [open, setOpen] = useState(false);
  const [working, setWorking] = useState(false);
  const eff = limit && windowMax ? Math.min(limit, windowMax) : windowMax ?? limit;
  const { data: info, mutate } = useSWR(open && cid ? `/api/v1/playground/conversations/${cid}/context?model_alias=${encodeURIComponent(modelAlias)}${limit ? `&context_limit=${limit}` : ""}` : null);
  const { data: conv, mutate: mutConv } = useSWR(open && cid ? `/api/v1/playground/conversations/${cid}` : null);
  const shown = used ?? info?.last_used ?? null;
  const pct = shown && eff ? Math.min(100, (shown / eff) * 100) : 0;
  const tone = pct > 85 ? "bg-danger" : pct > 60 ? "bg-warn" : "bg-accent";
  async function compact() {
    if (!cid) return;
    setWorking(true);
    try {
      const r = await api(`/api/v1/playground/conversations/${cid}/compact`, { method: "POST", json: { model_alias: modelAlias, context_limit: limit || null } });
      push("ok", r.compacted ? L(`Compactado: ${r.summarized_now} mensajes resumidos`, `Compacted: ${r.summarized_now} messages summarised`) : L("Aún no hay nada que resumir", "Nothing to summarise yet"));
      mutate(); mutConv(); onChanged?.();
    } catch (e: any) { push("error", e.message); } finally { setWorking(false); }
  }
  async function forget() {
    if (!cid) return;
    await api(`/api/v1/playground/conversations/${cid}/summary`, { method: "DELETE" });
    mutate(); mutConv(); onChanged?.();
  }
  return (
    <>
      <button type="button" onClick={() => setOpen(true)} title={L("Ventana de contexto: cuánto de la conversación ve el modelo", "Context window: how much of the conversation the model sees")}
        className="flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-lg border border-border px-2 py-1 text-[11px] text-muted hover:bg-elevated">
        <Gauge className="h-3.5 w-3.5" />
        <span className="hidden sm:inline">{L("Contexto", "Context")}</span>
        <span className="tabular-nums text-fg">{kTok(shown)}/{kTok(eff)}</span>
        <span className="h-1.5 w-10 overflow-hidden rounded bg-elevated"><span className={cx("block h-full", tone)} style={{ width: `${pct}%` }} /></span>
      </button>
      <Modal open={open} onOpenChange={setOpen} title={L("Ventana de contexto", "Context window")}
        description={L("Cuántos tokens de la conversación (mensajes, adjuntos, resultados de herramientas) puede ver el modelo en cada respuesta.", "How many tokens of the conversation (messages, attachments, tool results) the model can see per answer.")}>
        <div className="space-y-4 text-sm">
          <div>
            <div className="mb-1 flex justify-between text-xs text-muted"><span>{L("Usado en la última respuesta", "Used by the last answer")}</span><span className="tabular-nums">{kTok(shown)} / {kTok(eff)} tokens</span></div>
            <div className="h-2 overflow-hidden rounded bg-elevated"><div className={cx("h-full", tone)} style={{ width: `${pct}%` }} /></div>
            {info && <p className="mt-1 text-[11px] text-subtle">{L(`${info.messages_in_context} de ${info.messages} mensajes van completos al modelo (≈${kTok(info.history_tokens)} tokens)`, `${info.messages_in_context} of ${info.messages} messages are sent verbatim (≈${kTok(info.history_tokens)} tokens)`)}{info.summary ? L(` + resumen de ≈${kTok(info.summary_tokens)}`, ` + summary of ≈${kTok(info.summary_tokens)}`) : ""}.</p>}
          </div>
          <Field label={L("Tamaño de la ventana", "Window size")} hint={L(`Máximo de este modelo: ${kTok(windowMax)} tokens. Una ventana menor responde más rápido y deja más memoria libre al resto; una mayor recuerda más sin resumir.`, `This model's maximum: ${kTok(windowMax)} tokens. A smaller window answers faster; a larger one remembers more without summarising.`)}>
            <Select value={String(limit)} onChange={(e) => setLimit(Number(e.target.value))}>
              <option value="0">{L(`Máximo del modelo (${kTok(windowMax)})`, `Model maximum (${kTok(windowMax)})`)}</option>
              {Array.from(new Set([...SIZES, ...(limit ? [limit] : [])])).sort((a, b) => a - b).filter((s) => !windowMax || s < windowMax).map((s) => <option key={s} value={s}>{kTok(s)} tokens</option>)}
            </Select>
          </Field>
          <div className="rounded-xl border border-border p-3">
            <Switch checked={autoCompact} onChange={setAutoCompact} label={L("Compactar automáticamente", "Compact automatically")} />
            <p className="mt-1.5 text-xs text-muted">{L("Cuando la conversación ocupa más de la mitad de la ventana, el asistente resume los mensajes antiguos y sigue con el resumen + los últimos mensajes. Tus mensajes no se borran: siguen aquí y en las exportaciones.", "When the conversation takes more than half of the window, the assistant summarises the old messages and continues with the summary + the latest messages. Nothing is deleted: messages stay here and in exports.")}</p>
            {!autoCompact && <p className="mt-1.5 text-xs text-warn">{L("Sin compactar, si no cabe, el modelo deja de ver los mensajes más antiguos.", "Without compaction, if it does not fit, the model stops seeing the oldest messages.")}</p>}
          </div>
          {conv?.summary && (
            <details className="rounded-xl border border-border p-3 text-xs">
              <summary className="cursor-pointer font-medium text-fg"><Shrink className="mr-1 inline h-3.5 w-3.5" />{L(`Resumen actual (${conv.summary.messages} mensajes resumidos, ${conv.summary.compactions} compactaciones)`, `Current summary (${conv.summary.messages} messages, ${conv.summary.compactions} compactions)`)}</summary>
              <div className="prose-chat mt-2 max-h-60 overflow-y-auto"><ReactMarkdown remarkPlugins={[remarkGfm]}>{conv.summary.text}</ReactMarkdown></div>
              <Button size="sm" variant="ghost" className="mt-2" onClick={forget}>{L("Olvidar el resumen (el modelo vuelve a ver todo)", "Forget the summary (the model sees everything again)")}</Button>
            </details>
          )}
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" onClick={compact} loading={working} disabled={!cid || busy}><Shrink className="h-4 w-4" />{L("Compactar ahora", "Compact now")}</Button>
            <Button variant="primary" onClick={() => setOpen(false)}>{L("Hecho", "Done")}</Button>
          </div>
          {!cid && <Notice tone="muted">{L("Empieza una conversación para ver su uso de contexto.", "Start a conversation to see its context usage.")}</Notice>}
        </div>
      </Modal>
    </>
  );
}

const host = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return u; } };

export function WebSources({ sources }: { sources: any[] }) {
  const L = useL();
  return (
    <details className="border-t border-border/60 px-3 py-2"><summary className="cursor-pointer text-muted"><Globe className="mr-1 inline h-3 w-3" />{L("Fuentes web", "Web sources")} ({sources.length})</summary>
      <ul className="mt-2 space-y-1.5">{sources.map((s: any, i: number) => (
        <li key={i}><a href={s.url} target="_blank" rel="noreferrer noopener" className="font-medium text-fg hover:text-accent">[{i + 1}] {s.title || host(s.url)} <ExternalLink className="inline h-3 w-3" /></a> <span className="text-subtle">· {host(s.url)}</span>
          {s.snippet && <p className="line-clamp-2 text-muted">{s.snippet}</p>}</li>))}</ul>
    </details>
  );
}

export function AgentReport({ agent }: { agent: any }) {
  const L = useL();
  return (
    <div className="border-t border-border/60 px-3 py-2">
      <p className="text-muted"><Bot className="mr-1 inline h-3 w-3" /><span className="font-medium text-fg">{agent.name}</span> · {agent.task}</p>
      {agent.steps?.length > 0 && <ol className="mt-1.5 space-y-0.5 text-[11px] text-subtle">{agent.steps.map((s: any, i: number) => <li key={i}>{s.ok === false ? "⚠" : "✓"} {s.label}{s.summary ? ` — ${s.summary}` : ""}</li>)}</ol>}
      {agent.report && <details className="mt-1.5"><summary className="cursor-pointer text-muted">{L("Informe del agente", "Agent report")}</summary><div className="prose-chat mt-1 max-h-80 overflow-y-auto"><ReactMarkdown remarkPlugins={[remarkGfm]}>{agent.report}</ReactMarkdown></div></details>}
    </div>
  );
}

export function SkillNote({ skill, installed }: { skill: any; installed?: boolean }) {
  const L = useL();
  return (
    <div className="flex flex-wrap items-center gap-2 border-t border-border/60 px-3 py-2 text-muted">
      <Puzzle className="h-3.5 w-3.5" /><Badge tone="accent">{skill.name}</Badge>
      {skill.source_url && <a href={skill.source_url} target="_blank" rel="noreferrer noopener" className="truncate hover:text-accent">{host(skill.source_url)}</a>}
      {installed && <Link href="/skills" className="ml-auto text-accent">{L("Ver o desactivar en Skills", "View or disable in Skills")}</Link>}
    </div>
  );
}

export function CodeRun({ run }: { run: any }) {
  const L = useL();
  const imgs = (run.files ?? []).filter((f: any) => f.kind === "image");
  const others = (run.files ?? []).filter((f: any) => f.kind !== "image");
  return (
    <div className="border-t border-border/60 px-3 py-2">
      <details><summary className="cursor-pointer text-muted"><Code2 className="mr-1 inline h-3 w-3" />{L("Código", "Code")} ({run.language}){run.exit_code !== 0 || run.timed_out ? <span className="ml-1 text-warn">· {run.timed_out ? L("tiempo agotado", "timed out") : `exit ${run.exit_code}`}</span> : null}</summary>
        <pre className="mt-1 max-h-72 overflow-auto rounded-lg bg-elevated p-2 font-mono text-[11px]">{run.code}</pre></details>
      {(run.stdout || run.stderr) && <details open={!run.files?.length}><summary className="cursor-pointer text-muted">{L("Salida", "Output")}</summary>
        <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap rounded-lg bg-elevated p-2 font-mono text-[11px]">{run.stdout}{run.stderr ? `\n${run.stderr}` : ""}</pre></details>}
      {imgs.length > 0 && <div className="mt-2 flex flex-wrap gap-2">{imgs.map((f: any) => <a key={f.id} href={f.url} target="_blank" rel="noreferrer"><img src={f.url} alt={f.filename} className="max-h-[380px] rounded-lg border border-border bg-white" /></a>)}</div>}
      {(run.files ?? []).length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{[...others, ...imgs].map((f: any) => (
        <a key={f.id} href={`${f.url}?download=true`} className="inline-flex items-center gap-1 rounded-lg border border-border px-2 py-1 hover:bg-elevated"><FileText className="h-3 w-3" />{f.filename}<Download className="h-3 w-3 text-muted" /></a>))}
        <Link href="/files" className="self-center text-accent">{L("En Mis archivos → Resultados de código", "In My files → Code results")}</Link></div>}
    </div>
  );
}

export const TOOL_ICONS: Record<string, any> = { run_code: Code2, web_search: Globe, fetch_url: Globe, delegate_task: Bot, use_skill: Puzzle, read_skill_file: Puzzle, find_skills: Puzzle, install_skill: Puzzle, compact_context: Shrink };

// ------------------------------------------------------------------ voice (local Whisper / MMS-TTS on the Spark)

export function MicButton({ onText, lang }: { onText: (t: string) => void; lang?: string }) {
  const L = useL();
  const { push } = useToast();
  const [state, setState] = useState<"idle" | "rec" | "busy">("idle");
  const [secs, setSecs] = useState(0);
  const rec = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  useEffect(() => { if (state !== "rec") return; const t = setInterval(() => setSecs((s) => s + 1), 1000); return () => clearInterval(t); }, [state]);
  async function start() {
    if (!navigator.mediaDevices?.getUserMedia) { push("error", L("Este navegador no permite grabar audio (hace falta https).", "This browser cannot record audio (https required).")); return; }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const mime = MediaRecorder.isTypeSupported("audio/webm") ? "audio/webm" : "audio/mp4";
      const r = new MediaRecorder(stream, { mimeType: mime });
      chunks.current = [];
      r.ondataavailable = (e) => e.data.size && chunks.current.push(e.data);
      r.onstop = async () => {
        stream.getTracks().forEach((t) => t.stop());
        setState("busy");
        try {
          const fd = new FormData();
          fd.append("audio", new Blob(chunks.current, { type: mime }), mime === "audio/webm" ? "voz.webm" : "voz.m4a");
          if (lang) fd.append("language", lang === "en" ? "english" : "spanish");
          const res = await upload("/api/v1/voice/transcribe", fd);
          if (res.text) onText(res.text); else push("info", L("No se ha entendido nada", "Nothing was recognised"));
        } catch (e: any) { push("error", e.message); } finally { setState("idle"); }
      };
      rec.current = r; r.start(); setSecs(0); setState("rec");
    } catch { push("error", L("Permiso de micrófono denegado", "Microphone permission denied")); }
  }
  const stop = () => rec.current?.state === "recording" && rec.current.stop();
  return (
    <Button type="button" size="icon" variant={state === "rec" ? "danger" : "ghost"} onClick={state === "rec" ? stop : start} disabled={state === "busy"}
      title={state === "rec" ? L("Parar y transcribir", "Stop and transcribe") : L("Dictar (se transcribe en el Spark)", "Dictate (transcribed on the Spark)")}>
      {state === "busy" ? <Loader2 className="h-4 w-4 animate-spin" /> : state === "rec" ? <span className="flex items-center gap-1 text-[10px] tabular-nums"><Square className="h-3 w-3" />{secs}s</span> : <Mic className="h-4 w-4" />}
    </Button>
  );
}

let playing: HTMLAudioElement | null = null;

export async function speakText(text: string, lang?: string): Promise<HTMLAudioElement> {
  const blob = await postBlob("/api/v1/voice/speak", { text, lang });
  playing?.pause();
  const a = new Audio(URL.createObjectURL(blob));
  playing = a;
  await a.play();
  return a;
}

export function SpeakButton({ text, lang }: { text: string; lang?: string }) {
  const L = useL();
  const { push } = useToast();
  const [state, setState] = useState<"idle" | "busy" | "play">("idle");
  const audio = useRef<HTMLAudioElement | null>(null);
  async function toggle() {
    if (state === "play") { audio.current?.pause(); setState("idle"); return; }
    setState("busy");
    try {
      const a = await speakText(text, lang);
      audio.current = a; setState("play");
      a.onended = a.onpause = () => setState("idle");
    } catch (e: any) { push("error", e.message); setState("idle"); }
  }
  return (
    <button type="button" onClick={toggle} className="inline-flex items-center gap-1 text-[10px] text-subtle hover:text-accent" title={L("Leer en voz alta", "Read aloud")}>
      {state === "busy" ? <Loader2 className="h-3 w-3 animate-spin" /> : state === "play" ? <Square className="h-3 w-3" /> : <Volume2 className="h-3 w-3" />}
      {state === "play" ? L("Parar", "Stop") : L("Escuchar", "Listen")}
    </button>
  );
}
