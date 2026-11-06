"use client";
// The agent chosen for a chat. A menu of its own (not the browser's select) so the text is readable in the light and the dark theme.
import * as DM from "@radix-ui/react-dropdown-menu";
import { Bot, Check, ChevronDown } from "lucide-react";
import { cx } from "@/components/ui";
import { useL } from "@/lib/i18n";

export type AgentOption = { id: string; name: string; builtin?: boolean; mine?: boolean; description?: string };

function Item({ selected, onSelect, name, hint }: { selected: boolean; onSelect: () => void; name: string; hint?: string }) {
  return (
    <DM.Item onSelect={onSelect} className="flex cursor-pointer items-start gap-2 rounded-lg px-2.5 py-2 text-sm text-fg outline-none data-[highlighted]:bg-elevated">
      <Check className={cx("mt-0.5 h-3.5 w-3.5 shrink-0 text-accent", !selected && "invisible")} />
      <span className="min-w-0"><span className="block truncate font-medium">{name}</span>{hint && <span className="mt-0.5 line-clamp-2 block text-xs text-muted">{hint}</span>}</span>
    </DM.Item>
  );
}

export function AgentPicker({ value, agents, onChange, disabled }: { value: string; agents: AgentOption[]; onChange: (v: string) => void; disabled?: boolean }) {
  const L = useL();
  const hub = agents.filter((a) => a.builtin);
  const custom = agents.filter((a) => !a.builtin);
  const label = value || L("Automático", "Automatic");
  return (
    <DM.Root>
      <DM.Trigger asChild disabled={disabled}>
        <button type="button" aria-label={L("Agente preferido", "Preferred agent")} title={L("El agente elegido realizará la tarea principal; el asistente puede coordinar otros agentes si hacen falta", "The selected agent handles the main task; the assistant may coordinate other agents when needed")}
          className="inline-flex h-7 max-w-[210px] items-center gap-1.5 rounded-full border border-border bg-surface px-2.5 text-xs text-fg transition-colors hover:bg-elevated focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/40 disabled:opacity-50">
          <Bot className={cx("h-3.5 w-3.5 shrink-0", value ? "text-accent" : "text-muted")} />
          <span className="truncate">{label}</span>
          <ChevronDown className="h-3 w-3 shrink-0 text-muted" />
        </button>
      </DM.Trigger>
      <DM.Portal>
        <DM.Content align="start" sideOffset={6} className="z-[90] max-h-[60vh] w-[300px] overflow-y-auto rounded-xl border border-border bg-surface p-1 text-fg shadow-card">
          <Item selected={!value} onSelect={() => onChange("")} name={L("Automático", "Automatic")} hint={L("El asistente decide si necesita un agente", "The assistant decides whether it needs an agent")} />
          {hub.length > 0 && <>
            <DM.Label className="px-2.5 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-muted">{L("Agentes del hub", "Hub agents")}</DM.Label>
            {hub.map((a) => <Item key={a.id} selected={value === a.name} onSelect={() => onChange(a.name)} name={a.name} hint={a.description} />)}
          </>}
          {custom.length > 0 && <>
            <DM.Label className="px-2.5 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wide text-muted">{L("Agentes personalizados", "Custom agents")}</DM.Label>
            {custom.map((a) => <Item key={a.id} selected={value === a.name} onSelect={() => onChange(a.name)} name={a.name + (a.mine ? L(" (mío)", " (mine)") : "")} hint={a.description} />)}
          </>}
        </DM.Content>
      </DM.Portal>
    </DM.Root>
  );
}
