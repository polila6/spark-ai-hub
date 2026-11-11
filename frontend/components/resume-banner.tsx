"use client";
// Shown when the server restarted in the middle of a long task: its progress was saved every few steps, so one click puts it back
// as a message and the assistant goes on from there (the workspace files are exactly as they were left).
import { History, Loader2, Pause, Play, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import { useL } from "@/lib/i18n";

export type Checkpoint = { step: number; updated_at: string; tools: number; comments: number };

export const RESUME_TEXT = { es: "Continúa la tarea desde donde se interrumpió (el servidor se reinició). Revisa tu plan y el estado real del workspace, no repitas lo ya hecho y sigue hasta cumplir todos los objetivos.",
  en: "Carry on with the task from where it was interrupted (the server restarted). Check your plan and the real state of the workspace, do not repeat what is done, and go on until every goal is met." };

export function ResumeBanner({ cid, ckpt, onResume, onDone }: { cid: string; ckpt: Checkpoint; onResume: () => Promise<void> | void; onDone: () => void }) {
  const L = useL();
  const [busy, setBusy] = useState<"resume" | "discard" | null>(null);
  const [err, setErr] = useState("");
  const when = new Date(ckpt.updated_at).toLocaleString();
  async function resume() {
    setBusy("resume"); setErr("");
    try { await api(`/api/v1/playground/conversations/${cid}/resume`, { method: "POST" }); onDone(); await onResume(); }
    catch (e: any) { setErr(e.message ?? String(e)); setBusy(null); }
  }
  async function discard() {
    setBusy("discard"); setErr("");
    try { await api(`/api/v1/playground/conversations/${cid}/checkpoint`, { method: "DELETE" }); onDone(); }
    catch (e: any) { setErr(e.message ?? String(e)); setBusy(null); }
  }
  return (
    <div role="status" className="mx-3 my-2 rounded-xl border border-warn/40 bg-warn/8 p-3 text-xs sm:mx-6">
      <div className="flex items-start gap-2">
        <History className="mt-0.5 h-4 w-4 shrink-0 text-warn" />
        <div className="min-w-0 flex-1">
          <p className="font-medium text-fg">{L("La tarea se interrumpió (reinicio del servidor)", "The task was interrupted (server restart)")}</p>
          <p className="mt-0.5 text-muted">{L(`Se guardó el progreso hasta el paso ${ckpt.step} (${ckpt.tools} acciones, ${ckpt.comments} comentarios · ${when}). Los archivos del workspace siguen como estaban.`,
            `Progress was saved up to step ${ckpt.step} (${ckpt.tools} actions, ${ckpt.comments} comments · ${when}). The workspace files are as they were.`)}</p>
          {err && <p className="mt-1 text-danger">{err}</p>}
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" variant="primary" disabled={!!busy} onClick={resume}>{busy === "resume" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <History className="h-3.5 w-3.5" />}{L("Reanudar la tarea", "Resume the task")}</Button>
            <Button size="sm" variant="ghost" disabled={!!busy} onClick={discard}><X className="h-3.5 w-3.5" />{L("Descartar", "Discard")}</Button>
          </div>
        </div>
      </div>
    </div>
  );
}

export const CONTINUE_TEXT = { es: "Continúa la tarea desde donde la pausaste. Revisa tu plan y el estado real del workspace, no repitas lo ya hecho y sigue hasta cumplir todos los objetivos.",
  en: "Carry on with the task from where you paused it. Check your plan and the real state of the workspace, do not repeat what is done, and go on until every goal is met." };

/** «Pausar»: the task stops at the end of the step it is in (nothing is cut), the work is kept, «Reanudar» goes on. Safe moment to restart the server. */
export function PauseButton({ cid, busy }: { cid: string | null; busy: boolean }) {
  const L = useL();
  const [asked, setAsked] = useState(false);
  useEffect(() => { if (!busy) setAsked(false); }, [busy]);
  return (
    <Button type="button" size="sm" variant="ghost" disabled={!cid || asked}
      title={L("Se detiene al terminar el paso actual y guarda lo hecho; luego puedes reanudarla (también tras reiniciar el servidor)", "Stops when the current step ends and keeps the work; you can resume it later (also after a server restart)")}
      onClick={async () => { if (!cid) return; setAsked(true); await api(`/api/v1/playground/conversations/${cid}/pause`, { method: "POST" }).catch(() => setAsked(false)); }}>
      {asked ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Pause className="h-3.5 w-3.5" />}
      <span className="hidden sm:inline">{asked ? L("Pausando…", "Pausing…") : L("Pausar", "Pause")}</span>
    </Button>
  );
}

/** Under the last answer when the task was paused. */
export function PausedNotice({ steps, onResume }: { steps?: number; onResume: () => void }) {
  const L = useL();
  return (
    <div role="status" className="mt-2 flex flex-wrap items-center gap-2 rounded-xl border border-info/40 bg-info/8 px-3 py-2 text-xs">
      <Pause className="h-4 w-4 shrink-0 text-info" />
      <span className="min-w-0 flex-1 text-muted"><span className="font-medium text-fg">{L("Tarea en pausa", "Task paused")}</span>{steps ? L(` en el paso ${steps}`, ` at step ${steps}`) : ""}. {L("Lo hecho está guardado.", "The work is saved.")}</span>
      <Button size="sm" variant="primary" onClick={onResume}><Play className="h-3.5 w-3.5" />{L("Reanudar", "Resume")}</Button>
    </div>
  );
}
