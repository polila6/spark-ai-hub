"use client";
// How much the assistant thinks, and what it may use. Thinking, internet, deep research… are permissions: «Auto» lets a
// director decide, before each answer, what that message needs (see backend services/director.py); «Siempre» and «No»
// override it. The slider sets how far the director may go, from instant answers to maximum reasoning.
import { Brain, Compass, Globe, Telescope } from "lucide-react";
import { Tip, cx } from "@/components/ui";
import { useL } from "@/lib/i18n";
import { usePersisted } from "@/components/workspace/common";

export type PermState = "auto" | "on" | "off";
export type Effort = { level: number; director: boolean; allow: { think: PermState; web: PermState; research: PermState } };
export const DEFAULT_EFFORT: Effort = { level: 2, director: true, allow: { think: "auto", web: "auto", research: "off" } };

// (v2: everybody starts again at «Equilibrado»; a slider left at «Instantáneo» by accident made answers invent facts)
export const useEffort = (key = "sah-effort-v2") => usePersisted<Effort>(key, DEFAULT_EFFORT);

/** Fields of a message request for this setting (the old booleans are kept for older servers). */
export function effortBody(e: Effort) {
  return {
    effort: e.level, director: e.director,
    allow: { ...e.allow, docs: "auto", skills: "auto", agents: "auto", code: "auto" },
    web: e.allow.web !== "off", research: e.allow.research === "on",
  };
}

/** Restore a conversation's saved setting (conversation.settings holds what its last message sent). */
export function effortFrom(settings: any, base: Effort): Effort {
  const a = settings?.allow;
  if (!a && typeof settings?.effort !== "number") return base;
  const ok = (v: any, d: PermState): PermState => (v === "auto" || v === "on" || v === "off" ? v : d);
  return { level: typeof settings?.effort === "number" ? settings.effort : base.level, director: settings?.director ?? base.director,
    allow: { think: ok(a?.think, base.allow.think), web: ok(a?.web, base.allow.web), research: ok(a?.research, base.allow.research) } };
}

const NAMES: [string, string][] = [["Instantáneo", "Instant"], ["Rápido", "Fast"], ["Equilibrado", "Balanced"], ["Profundo", "Deep"], ["Máximo", "Maximum"]];
const HINTS: [string, string][] = [
  ["Responde ya: sin razonar y sin herramientas salvo las que marques «Siempre».", "Answers now: no reasoning and no tools except those set to Always."],
  ["Muy rápido: decide con reglas sencillas, razona solo si lo marcas «Siempre».", "Very fast: decides with simple rules, reasons only if set to Always."],
  ["Un director decide qué necesita cada mensaje: solo razona y busca cuando compensa.", "A director decides what each message needs: it reasons and searches only when worth it."],
  ["Razona en casi todo lo que no sea una charla corta; usa lo que haga falta.", "Reasons on almost anything beyond small talk; uses what is needed."],
  ["Razonamiento máximo y todo el tiempo necesario: la mejor precisión, no la más rápida.", "Maximum reasoning and all the time needed: best accuracy, not the fastest."],
];

export function EffortSlider({ value, onChange, compact }: { value: Effort; onChange: (e: Effort) => void; compact?: boolean }) {
  const L = useL();
  const [es, en] = NAMES[value.level] ?? NAMES[2];
  return (
    <div className="flex items-center gap-2">
      <Tip text={L(HINTS[value.level]?.[0] ?? "", HINTS[value.level]?.[1] ?? "")}>
        <label className="flex items-center gap-2 text-xs text-muted">
          {!compact && <span className="hidden sm:inline">{L("Respuesta", "Answer")}</span>}
          <span className="text-[10px] text-subtle">⚡</span>
          <input type="range" min={0} max={4} step={1} value={value.level} aria-label={L("Cuánto piensa", "How much it thinks")}
            aria-valuetext={L(es, en)} onChange={(e) => onChange({ ...value, level: Number(e.target.value) })}
            className={cx("h-1.5 cursor-pointer accent-accent", compact ? "w-16" : "w-24")} />
          <span className="text-[10px] text-subtle">🧠</span>
          <span className="w-[4.5rem] font-medium text-fg">{L(es, en)}</span>
        </label>
      </Tip>
      <Tip text={L("El director decide antes de contestar qué herramientas y cuánto razonamiento necesita este mensaje, para responder lo más rápido posible con la mejor precisión. Apágalo para ofrecer siempre todo lo permitido.",
        "The director decides before answering which tools and how much reasoning this message needs, to answer as fast as possible with the best accuracy. Turn it off to always offer everything allowed.")}>
        <button type="button" role="switch" aria-checked={value.director} aria-label={L("Director", "Director")} onClick={() => onChange({ ...value, director: !value.director })}
          className={cx("inline-flex h-7 items-center gap-1 rounded-full border px-2 text-xs transition-colors", value.director ? "border-accent/40 bg-accent/12 font-medium text-fg" : "border-transparent text-muted hover:bg-elevated")}>
          <Compass className={cx("h-3.5 w-3.5", value.director && "text-accent")} />{!compact && <span>{L("Director", "Director")}</span>}
        </button>
      </Tip>
    </div>
  );
}

const NEXT: Record<PermState, PermState> = { auto: "on", on: "off", off: "auto" };
const STATE_LABEL: Record<PermState, [string, string]> = { auto: ["auto", "auto"], on: ["siempre", "always"], off: ["no", "off"] };

/** A permission chip: click cycles Auto (it may use it, the director decides) → Always → No. */
export function PermChip({ state, onChange, icon: Icon, label, tip }: { state: PermState; onChange: (s: PermState) => void; icon: any; label: string; tip: string }) {
  const L = useL();
  const [es, en] = STATE_LABEL[state];
  return (
    <Tip text={`${tip} ${L("Clic para cambiar: Auto (puede usarlo si hace falta) → Siempre → No.", "Click to change: Auto (may use it when needed) → Always → Off.")}`}>
      <button type="button" aria-label={`${label}: ${L(es, en)}`} onClick={() => onChange(NEXT[state])}
        className={cx("inline-flex h-7 items-center gap-1.5 rounded-full border px-2.5 text-xs transition-colors",
          state === "on" ? "border-accent/50 bg-accent/15 font-medium text-fg" : state === "auto" ? "border-accent/25 text-fg hover:bg-elevated" : "border-transparent text-muted hover:bg-elevated")}>
        <Icon className={cx("h-3.5 w-3.5", state !== "off" && "text-accent")} />
        <span className={cx(state === "off" && "line-through")}>{label}</span>
        <span className={cx("rounded px-1 text-[10px]", state === "auto" ? "bg-accent/12 text-accent" : state === "on" ? "bg-accent text-accent-fg" : "text-subtle")}>{L(es, en)}</span>
      </button>
    </Tip>
  );
}

/** The three permission chips; `vllm` = the model can reason, `web` / `research` = the hub offers them. */
export function PermChips({ value, onChange, canThink, canWeb, canResearch }: { value: Effort; onChange: (e: Effort) => void; canThink: boolean; canWeb: boolean; canResearch: boolean }) {
  const L = useL();
  const set = (k: keyof Effort["allow"]) => (s: PermState) => onChange({ ...value, allow: { ...value.allow, [k]: s } });
  return <>
    {canThink && <PermChip state={value.allow.think} onChange={set("think")} icon={Brain} label={L("Pensar", "Think")} tip={L("Razonar antes de responder: más lento, mejor en problemas difíciles.", "Reason before answering: slower, better for hard problems.")} />}
    {canWeb && <PermChip state={value.allow.web} onChange={set("web")} icon={Globe} label={L("Internet", "Web")} tip={L("Buscar y leer páginas web.", "Search and read web pages.")} />}
    {canResearch && value.allow.web !== "off" && <PermChip state={value.allow.research} onChange={set("research")} icon={Telescope} label={L("Investigar", "Research")} tip={L("Investigación a fondo: varios agentes y un informe largo con fuentes.", "Deep research: several agents and a long report with sources.")} />}
  </>;
}

const TOOL_NAMES: Record<string, [string, string]> = { docs: ["tus documentos", "your documents"], web: ["internet", "web"], research: ["investigación", "research"], skills: ["skills", "skills"], agents: ["agentes", "agents"], code: ["código", "code"] };

/** «Director: razona · internet — pide datos actuales» */
export function directorText(d: any, L: (es: string, en: string) => string): string {
  if (!d) return "";
  const tools = (d.tools ?? []).map((t: string) => L(...(TOOL_NAMES[t] ?? [t, t]))).join(", ");
  return `${d.think ? L("razona", "reasons") : L("sin razonar", "no reasoning")} · ${tools || L("sin herramientas", "no tools")}${d.reason ? ` — ${d.reason}` : ""}`;
}
