"use client";
// Workspace terminal: every command (one line or a whole script) runs on the server in the sandbox, in the
// background, with the workspace as the current folder. Runs live in Redis, so they survive leaving the page.
import { ChevronDown, CircleStop, CornerDownLeft, Loader2, Play, RotateCcw, SquarePen, SquareTerminal, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { cx } from "@/components/ui";
import { duration } from "@/lib/format";
import { useL } from "@/lib/i18n";
import { type Run, handleIndent, newlineKeepIndent, useAutoGrow, useNow } from "./common";

export const MAX_RUNNING = 3;

export function Terminal({ runs, onRun, onStop, onOpen, onClear, codeEnabled, timeoutMin, packages, actions }: {
  runs: Run[]; onRun: (cmd: string) => Promise<boolean>; onStop: (id: string) => void; onOpen: (p: string) => void; onClear: () => void;
  codeEnabled: boolean; timeoutMin?: number; packages?: boolean; actions?: React.ReactNode;
}) {
  const L = useL();
  const [cmd, setCmd] = useState("");
  const [hist, setHist] = useState(-1);
  const stash = useRef("");
  const input = useRef<HTMLTextAreaElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const stick = useRef(true);
  const running = runs.filter((r) => r.running);
  const now = useNow(running.length > 0);
  const full = running.length >= MAX_RUNNING;
  useAutoGrow(input, cmd, 220);
  // follow the output like a terminal, unless the user scrolled up to read something
  useEffect(() => { const el = scroller.current; if (el && stick.current) el.scrollTop = el.scrollHeight; }, [runs]);
  const past = (() => { const out: string[] = []; for (let i = runs.length - 1; i >= 0; i--) if (runs[i].command !== out[out.length - 1]) out.push(runs[i].command); return out; })();

  async function submit() {
    const c = cmd.replace(/^\s*\n/, "").replace(/\s+$/, "");
    if (!c.trim() || full || !codeEnabled) return;
    stick.current = true;
    if (await onRun(c)) { setCmd(""); setHist(-1); stash.current = ""; }
  }
  function recall(h: number) {
    if (hist === -1) stash.current = cmd;
    setHist(h);
    const v = h >= 0 ? past[h] : stash.current;
    setCmd(v);
    requestAnimationFrame(() => { const el = input.current; if (el) el.setSelectionRange(v.length, v.length); });
  }
  const fill = (v: string) => { setCmd(v); setHist(-1); requestAnimationFrame(() => { input.current?.focus(); input.current?.setSelectionRange(v.length, v.length); }); };

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#0d1117] text-[12px] text-[#c9d1d9]">
      <div className="flex h-9 shrink-0 items-center gap-2 border-b border-[#30363d] px-3">
        <SquareTerminal className="h-3.5 w-3.5 text-[#8b949e]" />
        <span className="text-xs font-medium text-[#e6edf3]">{L("Terminal", "Terminal")}</span>
        {running.length > 0 && <span className="flex items-center gap-1 rounded-full bg-[#1f6feb]/20 px-2 py-0.5 text-[10.5px] text-[#79c0ff]"><Loader2 className="h-3 w-3 animate-spin" />{L(`${running.length} en marcha`, `${running.length} running`)}</span>}
        <span className="hidden truncate text-[10.5px] text-[#6e7681] md:inline">/work · {packages ? L("internet solo para pip/npm", "internet only for pip/npm") : L("sin internet", "no internet")}{timeoutMin ? ` · ${L("hasta", "up to")} ${timeoutMin} min` : ""}</span>
        <div className="ml-auto flex items-center gap-0.5">
          {runs.some((r) => !r.running) && <button type="button" className="rounded p-1 text-[#8b949e] hover:bg-[#30363d] hover:text-[#e6edf3]" title={L("Limpiar el historial (los comandos en marcha se quedan)", "Clear the history (running commands stay)")} onClick={onClear}><Trash2 className="h-3.5 w-3.5" /></button>}
          {actions}
        </div>
      </div>
      <div ref={scroller} className="min-h-0 flex-1 overflow-y-auto px-3 py-2 font-mono"
        onScroll={(e) => { const el = e.currentTarget; stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60; }}>
        {runs.length === 0 ? (
          <div className="space-y-2 py-1 font-sans text-[12px] text-[#8b949e]">
            <p>{L("Escribe un comando o un script de varias líneas; se ejecuta en el Spark con la raíz del workspace como carpeta actual. Ejemplos:", "Type a command or a multi-line script; it runs on the Spark with the workspace root as the current folder. Examples:")}</p>
            <div className="flex flex-wrap gap-1.5 font-mono text-[11.5px]">
              {["ls -la", "python script.py", "pip install requests", "for f in *.csv; do\n  wc -l \"$f\"\ndone", "python - <<'EOF'\nimport pandas as pd\nprint(pd.__version__)\nEOF"].map((x) => (
                <button key={x} type="button" onClick={() => fill(x)} className="max-w-full truncate rounded-md border border-[#30363d] px-2 py-1 text-left text-[#c9d1d9] hover:border-[#8b949e]" title={x}>
                  {x.split("\n")[0]}{x.includes("\n") && <span className="text-[#6e7681]"> …</span>}
                </button>))}
            </div>
          </div>
        ) : runs.map((r) => <RunBlock key={r.id} r={r} now={now} onStop={onStop} onOpen={onOpen} onEdit={fill} onRerun={(c) => onRun(c)} canRun={!full && codeEnabled} />)}
      </div>
      <form className="shrink-0 border-t border-[#30363d] px-3 pb-1.5 pt-2" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <div className="flex items-start gap-2">
          <span className="select-none pt-[3px] font-mono text-[#7ee787]">$</span>
          <textarea ref={input} rows={1} spellCheck={false} autoCapitalize="off" autoCorrect="off" value={cmd} aria-label={L("Comando o script", "Command or script")}
            placeholder={!codeEnabled ? L("La ejecución de código está desactivada", "Code execution is disabled") : "python script.py"}
            className="min-w-0 flex-1 resize-none bg-transparent py-0.5 font-mono text-[12.5px] leading-[1.55] text-[#e6edf3] outline-none placeholder:text-[#6e7681]"
            onChange={(e) => { setCmd(e.target.value); if (hist !== -1) setHist(-1); }}
            onKeyDown={(e) => {
              const el = e.currentTarget;
              if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                if (e.shiftKey) { e.preventDefault(); newlineKeepIndent(el); return; }
                if (!e.altKey) { e.preventDefault(); submit(); return; }
              }
              if (handleIndent(e, "    ")) return;
              if (e.key === "Escape") { el.blur(); return; }
              const collapsed = el.selectionStart === el.selectionEnd;
              if (e.key === "ArrowUp" && collapsed && !el.value.slice(0, el.selectionStart).includes("\n") && hist < past.length - 1) { e.preventDefault(); recall(hist + 1); }
              if (e.key === "ArrowDown" && collapsed && !el.value.slice(el.selectionEnd).includes("\n") && hist > -1) { e.preventDefault(); recall(hist - 1); }
            }} />
          <button type="submit" disabled={!cmd.trim() || full || !codeEnabled} title={L("Ejecutar (Enter)", "Run (Enter)")}
            className="flex h-6 shrink-0 items-center gap-1 rounded-md bg-[#238636] px-2 text-[11px] font-medium text-white transition hover:bg-[#2ea043] disabled:cursor-not-allowed disabled:opacity-40">
            <Play className="h-3 w-3" />{L("Ejecutar", "Run")}
          </button>
        </div>
        <p className="mt-1 flex flex-wrap gap-x-3 pl-4 text-[10.5px] text-[#6e7681]">
          {full ? <span className="text-[#d29922]">{L(`Hay ${MAX_RUNNING} comandos en marcha: espera o para alguno para lanzar otro.`, `${MAX_RUNNING} commands are running: wait or stop one to start another.`)}</span> : <>
            <span><Kbd>Enter</Kbd> {L("ejecuta", "runs")}</span>
            <span><Kbd>{L("Mayús", "Shift")}+Enter</Kbd> {L("nueva línea", "new line")}</span>
            <span><Kbd>Tab</Kbd> {L("sangra", "indents")}</span>
            <span><Kbd>↑↓</Kbd> {L("historial", "history")}</span>
          </>}
        </p>
      </form>
    </div>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return <kbd className="rounded border border-[#30363d] px-1 font-sans text-[10px] text-[#8b949e]">{children}</kbd>;
}

function RunBlock({ r, now, onStop, onOpen, onEdit, onRerun, canRun }: {
  r: Run; now: number; onStop: (id: string) => void; onOpen: (p: string) => void; onEdit: (c: string) => void; onRerun: (c: string) => void; canRun: boolean;
}) {
  const L = useL();
  const [open, setOpen] = useState(true);
  const [allLines, setAllLines] = useState(false);
  const lines = r.command.split("\n");
  const shown = allLines ? lines : lines.slice(0, 6);
  const elapsed = r.started_at ? Math.max(0, (now - Date.parse(r.started_at)) / 1000) : undefined;
  const stopped = r.stopped || (r.stop_requested && !r.running);
  const ok = !r.running && !r.timed_out && !stopped && r.exit_code === 0;
  const status = r.running ? null
    : stopped ? { t: L("detenido", "stopped"), c: "text-[#d29922]" }
    : r.timed_out ? { t: L("tiempo agotado", "timed out"), c: "text-[#d29922]" }
    : r.oom_killed ? { t: L("sin memoria", "out of memory"), c: "text-[#ff7b72]" }
    : ok ? { t: "exit 0", c: "text-[#7ee787]" } : { t: `exit ${r.exit_code}`, c: "text-[#ff7b72]" };
  return (
    <div className={cx("group mb-2 rounded-md border-l-2 pl-2", r.running ? "border-[#1f6feb]" : ok ? "border-[#238636]/70" : "border-[#da3633]/70")}>
      <div className="flex items-start gap-1.5">
        <button type="button" onClick={() => setOpen((o) => !o)} className="mt-[2px] text-[#6e7681] hover:text-[#c9d1d9]" aria-label={open ? L("Plegar", "Collapse") : L("Desplegar", "Expand")}>
          <ChevronDown className={cx("h-3 w-3 transition-transform", !open && "-rotate-90")} />
        </button>
        <pre className="min-w-0 flex-1 whitespace-pre-wrap break-words text-[#7ee787]">{shown.map((l, i) => <span key={i}>{i === 0 ? "$ " : "> "}{l}{"\n"}</span>)}
          {lines.length > 6 && <button type="button" className="text-[#8b949e] underline hover:text-white" onClick={() => setAllLines((a) => !a)}>{allLines ? L("ver menos", "show less") : L(`… ${lines.length - 6} líneas más`, `… ${lines.length - 6} more lines`)}</button>}</pre>
        <div className="flex shrink-0 items-center gap-1 font-sans text-[10.5px]">
          {r.running ? <>
            <span className="flex items-center gap-1 text-[#79c0ff]"><Loader2 className="h-3 w-3 animate-spin" />{r.stop_requested ? L("parando…", "stopping…") : duration(elapsed)}</span>
            {!r.stop_requested && <button type="button" onClick={() => onStop(r.id)} className="flex items-center gap-1 rounded border border-[#da3633]/60 px-1.5 py-0.5 text-[#ff7b72] hover:bg-[#da3633]/20" title={L("Parar este comando", "Stop this command")}><CircleStop className="h-3 w-3" />{L("Parar", "Stop")}</button>}
          </> : <>
            <span className={status?.c}>{status?.t}</span><span className="text-[#6e7681]">· {duration(r.seconds)}</span>
            <span className="flex opacity-0 transition group-hover:opacity-100 [@media(hover:none)]:opacity-100">
              <button type="button" onClick={() => onEdit(r.command)} className="rounded p-0.5 text-[#8b949e] hover:bg-[#30363d] hover:text-white" title={L("Editar y volver a lanzar", "Edit and run again")}><SquarePen className="h-3 w-3" /></button>
              {canRun && <button type="button" onClick={() => onRerun(r.command)} className="rounded p-0.5 text-[#8b949e] hover:bg-[#30363d] hover:text-white" title={L("Repetir", "Run again")}><RotateCcw className="h-3 w-3" /></button>}
            </span>
          </>}
        </div>
      </div>
      {open && !r.running && <div className="pl-[18px]">
        {r.stdout && <pre className="whitespace-pre-wrap break-all">{r.stdout}</pre>}
        {r.stderr && <pre className="whitespace-pre-wrap break-all text-[#ff7b72]">{r.stderr}</pre>}
        {!r.stdout && !r.stderr && <p className="font-sans text-[10.5px] italic text-[#6e7681]">{L("(sin salida)", "(no output)")}</p>}
        {r.changed && r.changed.length > 0 && <div className="mt-0.5 flex flex-wrap items-center gap-1 font-sans text-[10.5px] text-[#8b949e]">
          <CornerDownLeft className="h-3 w-3" />{L("cambiados", "changed")}:
          {r.changed.slice(0, 12).map((p) => <button key={p} type="button" className="rounded border border-[#30363d] px-1 font-mono hover:border-[#8b949e] hover:text-white" onClick={() => onOpen(p)}>{p}</button>)}
          {r.changed.length > 12 && <span>+{r.changed.length - 12}</span>}
        </div>}
      </div>}
    </div>
  );
}
