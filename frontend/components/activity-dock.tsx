"use client";
// Floating «Actividad» window: while the assistant works it keeps the plan, the running commands and what every agent is doing
// within reach, apart from the chat (no need to scroll up). A pill bottom-right opens it: a card on large screens, a bottom
// sheet on phones. Three tabs — Tareas (plan + steps), Agentes (one card per delegated agent with its live steps and report),
// Comandos (terminal and code runs with their output). Fed with the same tools/activity the chat message already has.
import { Activity as ActivityIcon, Bot, Check, ChevronDown, ChevronRight, ListChecks, Loader2, Terminal, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { AgentReport } from "@/components/assistant";
import { Activity, dur, Plan, runningLabel, StepRow, useNow } from "@/components/live-activity";
import { cx } from "@/components/ui";
import { useL } from "@/lib/i18n";

type Tab = "tasks" | "agents" | "cmds";
const KEY = "sah-dock";
const CMD = new Set(["ws_run", "run_code"]);

function AgentCard({ t, now, live }: { t: any; now: number; live: boolean }) {
  const L = useL();
  const [open, setOpen] = useState(false);
  const running = !t.done && live;
  const name = t.agent?.name ?? t.args?.agent ?? "agent";
  const task = t.agent?.task ?? t.args?.task ?? "";
  const secs = running ? (t.t0 ? (now - t.t0) / 1000 : undefined) : t.seconds;
  const log: string[] = t.log ?? [];
  const steps: any[] = t.agent?.steps ?? [];
  return (
    <li className={cx("rounded-xl border", running ? "border-info/40 bg-info/5" : t.ok === false ? "border-warn/40" : "border-border")}>
      <button type="button" className="flex w-full items-start gap-2 px-3 py-2 text-left" onClick={() => setOpen(!open)} aria-expanded={open}>
        <span className={cx("mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-md", running ? "bg-info/15 text-info" : t.ok === false ? "bg-warn/15 text-warn" : "bg-accent/12 text-accent")}><Bot className="h-3.5 w-3.5" /></span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2"><span className="font-medium text-fg">{name}</span>
            <span className="text-[10.5px] tabular-nums text-subtle">{secs != null && dur(secs)}</span></span>
          <span className={cx("block text-[11px] text-muted", !open && "line-clamp-2")}>{task}</span>
          {running && log.length > 0 && <span className="mt-0.5 block truncate text-[11px] text-info">{log[log.length - 1]}</span>}
          {!running && t.summary && <span className="mt-0.5 block truncate text-[11px] text-subtle">{t.summary}</span>}
        </span>
        <span className="mt-0.5 shrink-0">{running ? <Loader2 className="h-3.5 w-3.5 animate-spin text-info" /> : t.done ? <Check className="h-3.5 w-3.5 text-ok" /> : null}</span>
      </button>
      {open && <div className="border-t border-border/60 px-3 py-2 text-[11px]">
        {log.length > 0 && <><p className="mb-1 font-medium text-muted">{L("Qué va haciendo", "What it is doing")}</p>
          <ul className="max-h-60 space-y-0.5 overflow-y-auto text-subtle">{log.map((x, i) => <li key={i} className="break-words">· {x}</li>)}</ul></>}
        {steps.length > 0 && !log.length && <ol className="space-y-0.5 text-subtle">{steps.map((s, i) => <li key={i}>{s.ok === false ? "⚠" : "✓"} {s.label}{s.summary ? ` — ${s.summary}` : ""}</li>)}</ol>}
        {t.agent && <div className="-mx-3 -mb-2"><AgentReport agent={t.agent} /></div>}
        {!log.length && !t.agent && <p className="text-subtle">{L("Todavía no hay pasos.", "No steps yet.")}</p>}
      </div>}
    </li>
  );
}

export function ActivityDock({ tools, activity, running, onOpen }: { tools?: any[]; activity?: Activity; running: boolean; onOpen?: (p: string) => void }) {
  const L = useL();
  const now = useNow(running);
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<Tab>("tasks");
  useEffect(() => { try { const v = localStorage.getItem(KEY); if (v) setOpen(v === "1"); } catch { /* private mode */ } }, []);
  const toggle = (v: boolean) => { setOpen(v); try { localStorage.setItem(KEY, v ? "1" : "0"); } catch { /* private mode */ } };
  const all = useMemo(() => tools ?? [], [tools]);
  const agents = useMemo(() => all.filter((t) => t.name === "delegate_task"), [all]);
  const cmds = useMemo(() => all.filter((t) => CMD.has(t.name)), [all]);
  const plan = activity?.plan ?? [];
  const live = all.filter((t) => !t.done);
  const recent = all.filter((t) => t.done && t.name !== "delegate_task").slice(-8).reverse();
  const planDone = plan.filter((i) => i.status === "completed").length;
  const agentsLive = agents.filter((t) => !t.done).length;
  if (!running) return null;
  const tabs: [Tab, string, number][] = [["tasks", L("Tareas", "Tasks"), live.length], ["agents", L("Agentes", "Agents"), agentsLive], ["cmds", L("Comandos", "Commands"), cmds.filter((t) => !t.done).length]];
  return (
    <>
      {!open && (
        <button type="button" onClick={() => toggle(true)} aria-label={L("Abrir la ventana de actividad", "Open the activity window")}
          className="fixed bottom-[calc(var(--tabbar)+6.5rem)] right-3 z-40 flex items-center gap-2 rounded-full border border-info/40 bg-surface/95 px-3 py-2 text-xs shadow-lg backdrop-blur lg:bottom-24 lg:right-6">
          <Loader2 className="h-3.5 w-3.5 animate-spin text-info" />
          <span className="font-medium text-fg">{L("Actividad", "Activity")}</span>
          <span className="tabular-nums text-muted">{[live.length ? L(`${live.length} en curso`, `${live.length} running`) : null, agentsLive ? L(`${agentsLive} agentes`, `${agentsLive} agents`) : null, plan.length ? `${planDone}/${plan.length}` : null].filter(Boolean).join(" · ")}</span>
        </button>
      )}
      {open && (
        <section role="dialog" aria-label={L("Actividad en directo", "Live activity")}
          className="fixed inset-x-0 bottom-0 z-[60] flex max-h-[75dvh] flex-col rounded-t-2xl border border-border bg-surface shadow-2xl lg:inset-x-auto lg:bottom-24 lg:right-6 lg:max-h-[70vh] lg:w-[26rem] lg:rounded-2xl">
          <header className="flex items-center gap-2 border-b border-border px-3 py-2">
            <ActivityIcon className="h-4 w-4 text-info" />
            <h2 className="text-sm font-medium text-fg">{L("Actividad en directo", "Live activity")}</h2>
            {activity?.step != null && <span className="text-[10.5px] text-subtle">{L("Paso", "Step")} {activity.step}</span>}
            <button type="button" onClick={() => toggle(false)} aria-label={L("Minimizar", "Minimise")} className="ml-auto grid h-8 w-8 place-items-center rounded-lg text-muted hover:bg-elevated"><ChevronDown className="h-4 w-4" /></button>
          </header>
          <div role="tablist" className="flex gap-1 border-b border-border px-2 py-1.5">
            {tabs.map(([k, label, n]) => (
              <button key={k} role="tab" aria-selected={tab === k} type="button" onClick={() => setTab(k)}
                className={cx("flex min-h-8 items-center gap-1.5 rounded-lg px-3 text-xs", tab === k ? "bg-accent/12 font-medium text-accent" : "text-muted hover:bg-elevated")}>
                {k === "tasks" ? <ListChecks className="h-3.5 w-3.5" /> : k === "agents" ? <Bot className="h-3.5 w-3.5" /> : <Terminal className="h-3.5 w-3.5" />}{label}
                {n > 0 && <span className="rounded-full bg-info/15 px-1.5 text-[10px] tabular-nums text-info">{n}</span>}
              </button>))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto pb-[env(safe-area-inset-bottom)]">
            {tab === "tasks" && <>
              {plan.length > 0 ? <Plan items={plan} /> : <p className="px-3 py-3 text-xs text-subtle">{L("Aún no hay un plan; aparece cuando el asistente lo crea.", "No plan yet; it appears when the assistant makes one.")}</p>}
              <div className="border-t border-border/60 py-1">
                <p className="px-3 py-1 text-[11px] font-medium text-muted">{L("Ahora", "Now")}</p>
                {live.length === 0 ? <p className="px-3 pb-2 text-xs text-subtle">{activity?.phase === "thinking" ? L("Pensando…", "Thinking…") : activity?.phase === "writing" ? L("Redactando la respuesta…", "Writing the answer…") : L("Pensando el siguiente paso…", "Working out the next step…")}</p>
                  : <ol className="px-1">{live.map((t) => <StepRow key={t.id} t={t} n={all.indexOf(t) + 1} now={now} live onOpen={onOpen} />)}</ol>}
                {recent.length > 0 && <><p className="px-3 pb-1 pt-2 text-[11px] font-medium text-muted">{L("Últimos pasos", "Latest steps")}</p>
                  <ol className="px-1">{recent.map((t) => <StepRow key={t.id} t={t} n={all.indexOf(t) + 1} now={0} live onOpen={onOpen} />)}</ol></>}
              </div>
            </>}
            {tab === "agents" && (agents.length === 0
              ? <p className="px-3 py-4 text-xs text-subtle">{L("Ningún agente delegado por ahora. Cuando el asistente reparta trabajo, verás aquí cada agente con sus pasos en directo.", "No delegated agents yet. When the assistant hands out work, each agent shows here with its live steps.")}</p>
              : <ul className="space-y-2 p-3">{[...agents].reverse().map((t) => <AgentCard key={t.id} t={t} now={now} live />)}</ul>)}
            {tab === "cmds" && (cmds.length === 0
              ? <p className="px-3 py-4 text-xs text-subtle">{L("Todavía no se ha ejecutado ningún comando.", "No command has run yet.")}</p>
              : <ol className="px-1 py-1">{[...cmds].reverse().map((t) => <StepRow key={t.id} t={t} n={all.indexOf(t) + 1} now={now} live onOpen={onOpen} />)}</ol>)}
          </div>
          <button type="button" onClick={() => toggle(false)} className="sr-only"><X className="h-4 w-4" />{L("Cerrar", "Close")}</button>
        </section>
      )}
    </>
  );
}
