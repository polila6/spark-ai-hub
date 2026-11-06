"use client";
import { CancelLoadButton } from "@/components/cancel-load";
import { AudioLines, Brain, Cpu, Eye, Image as ImageIcon, Loader2, MemoryStick, Pin, Power, Zap } from "lucide-react";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useL } from "@/lib/i18n";
import { isManager, useMe, useToast } from "@/lib/providers";
import { Badge, Button, Card, CardBody, CardHeader, Modal, Notice, Progress, cx } from "./ui";

const KIND_COLOR: Record<string, string> = { llm: "bg-accent", image: "bg-fuchsia-500", embedding: "bg-sky-500", audio: "bg-amber-500", engine: "bg-slate-500" };
const KIND_ICON: Record<string, any> = { llm: Brain, image: ImageIcon, embedding: Zap, audio: AudioLines, engine: Cpu };

export function mmss(s: number) {
  const m = Math.floor(s / 60);
  return `${m}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

export function useMemory() {
  const { data, mutate } = useSWR("/api/v1/system/memory", {
    refreshInterval: (d: any) => (d?.instances?.some((i: any) => ["loading", "unloading"].includes(i.state)) ? 2000 : 8000),
  });
  return { mem: data, refresh: mutate };
}

/** Seconds since the snapshot, so countdowns and load progress tick between refreshes. */
export function useTick(dep: any) {
  const [t0, setT0] = useState(Date.now());
  const [, setN] = useState(0);
  useEffect(() => { setT0(Date.now()); }, [dep]);
  useEffect(() => { const id = setInterval(() => setN((n) => n + 1), 1000); return () => clearInterval(id); }, []);
  return (Date.now() - t0) / 1000;
}

export function MemoryBar({ mem, className }: { mem: any; className?: string }) {
  const total = mem?.schedulable_gb || 1;
  const segs = [
    ...(mem?.instances ?? []).map((i: any) => ({ key: i.id, gb: i.reserved_gb, cls: cx(KIND_COLOR[i.kind] ?? "bg-slate-500", i.state === "loading" && "animate-pulse opacity-70", i.pinned && "ring-1 ring-inset ring-white/50"), title: `${i.name} · ${i.reserved_gb} GB` })),
    ...(mem?.jobs ?? []).map((j: any) => ({ key: j.id, gb: j.reserved_gb, cls: "bg-orange-400", title: `${j.title} · ${j.reserved_gb} GB` })),
  ];
  return (
    <div className={cx("flex h-3 w-full overflow-hidden rounded-full bg-elevated", className)} role="img" aria-label="memory">
      {segs.map((s) => <div key={s.key} title={s.title} className={cx("h-full border-r border-surface/70", s.cls)} style={{ width: `${Math.min(100, (s.gb / total) * 100)}%` }} />)}
    </div>
  );
}

/** Full panel: budget bar, every resident model with state, idle countdown, load progress and unload button. */
export function MemoryPanel({ compact }: { compact?: boolean }) {
  const L = useL();
  const { me } = useMe();
  const { push } = useToast();
  const { mem, refresh } = useMemory();
  const dt = useTick(mem);
  if (!mem) return null;
  const used = mem.reserved_gb;
  const pct = (used / mem.schedulable_gb) * 100;
  return (
    <Card>
      <CardHeader title={L("Memoria del Spark", "Spark memory")} icon={<MemoryStick className="h-4 w-4" />}
        subtitle={L(`${used.toFixed(0)} de ${mem.schedulable_gb.toFixed(0)} GB reservados · ${mem.system_reserve_gb} GB para el sistema · los modelos extra se descargan tras ${mem.idle_timeout_min} min sin uso`,
          `${used.toFixed(0)} of ${mem.schedulable_gb.toFixed(0)} GB reserved · ${mem.system_reserve_gb} GB for the system · extra models unload after ${mem.idle_timeout_min} idle min`)}
        actions={<Badge tone={pct > 85 ? "danger" : pct > 65 ? "warn" : "ok"}>{L("libre", "free")} {mem.free_logical_gb.toFixed(0)} GB</Badge>} />
      <CardBody className="space-y-3">
        <MemoryBar mem={mem} />
        <div className="flex flex-wrap gap-3 text-[10px] text-muted">
          {[["llm", L("Modelos de lenguaje", "Language models")], ["image", L("Imagen/vídeo", "Image/video")], ["embedding", "Embeddings"], ["audio", "Audio"]].map(([k, l]) =>
            <span key={k} className="flex items-center gap-1"><span className={cx("h-2 w-2 rounded-full", KIND_COLOR[k])} />{l}</span>)}
          <span className="flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-orange-400" />{L("Trabajos", "Jobs")}</span>
        </div>
        {!mem.instances.length && !mem.jobs.length && <p className="text-sm text-muted">{L("No hay modelos cargados ahora mismo.", "No models loaded right now.")}</p>}
        <ul className="divide-y divide-border/60">
          {mem.instances.map((i: any) => {
            const Icon = KIND_ICON[i.kind] ?? Cpu;
            const elapsed = i.state === "loading" ? (Date.now() - new Date(i.started_at).getTime()) / 1000 : 0;
            const left = i.unload_in_seconds != null ? Math.max(0, i.unload_in_seconds - dt) : null;
            const canUnload = i.model_id && i.state === "ready" && (!i.pinned || isManager(me));
            return (
              <li key={i.id} className={cx("flex flex-wrap items-center gap-x-3 gap-y-1", compact ? "py-1.5" : "py-2.5")}>
                <span className={cx("grid h-7 w-7 shrink-0 place-items-center rounded-lg text-white", KIND_COLOR[i.kind] ?? "bg-slate-500")}><Icon className="h-3.5 w-3.5" /></span>
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-1.5 text-sm font-medium">{i.name}
                    {i.default && <Badge tone="accent"><Pin className="h-3 w-3" />{L("general · siempre cargado", "general · always loaded")}</Badge>}
                    {!i.default && i.pinned && <Badge tone="accent"><Pin className="h-3 w-3" />{L("fijado", "pinned")}</Badge>}
                    {i.vision && <Badge><Eye className="h-3 w-3" />vision</Badge>}
                    {i.active_requests > 0 && <Badge tone="info">{L("en uso", "in use")} · {i.active_requests}</Badge>}
                  </p>
                  {i.state === "loading" ? (
                    <div className="mt-1 flex items-center gap-2 text-[11px] text-info">
                      <Loader2 className="h-3 w-3 animate-spin" />{L("Cargando", "Loading")} {mmss(elapsed)}
                      {i.engine === "vllm" && i.model_id && (!i.pinned || isManager(me)) && <CancelLoadButton modelId={i.model_id} alias={i.name} onDone={refresh} />}
                      {i.expected_load_seconds ? <><Progress className="w-32" value={Math.min(97, (elapsed / i.expected_load_seconds) * 100)} tone="accent" /><span className="text-muted">≈ {mmss(Math.max(0, i.expected_load_seconds - elapsed))} {L("restantes", "left")}</span></> : null}
                    </div>
                  ) : i.state === "unloading" ? <p className="text-[11px] text-warn">{L("Descargando de memoria…", "Unloading…")}</p>
                    : left != null ? <p className="text-[11px] text-muted">{L("Inactivo", "Idle")} {mmss((i.idle_seconds ?? 0) + dt)} · {L("se descarga en", "unloads in")} <b className={left < 60 ? "text-warn" : ""}>{mmss(left)}</b></p>
                    : <p className="text-[11px] text-muted">{i.protected ? L("No se descarga automáticamente", "Never unloaded automatically") : i.active_requests ? L("Respondiendo", "Answering") : ""}</p>}
                </div>
                <span className="w-16 text-right text-sm tabular-nums">{i.reserved_gb.toFixed(1)} GB</span>
                {!compact && canUnload && <Button size="sm" variant="ghost" onClick={async () => { try { await api(`/api/v1/models/${i.model_id}/unload`, { method: "POST" }); push("ok", L(`${i.name} descargado`, `${i.name} unloaded`)); refresh(); } catch (e: any) { push("error", e.message); } }}><Power className="h-3.5 w-3.5" />{L("Descargar", "Unload")}</Button>}
              </li>
            );
          })}
          {mem.jobs.map((j: any) => (
            <li key={j.id} className="flex items-center gap-3 py-2">
              <span className="grid h-7 w-7 place-items-center rounded-lg bg-orange-400 text-white"><Loader2 className="h-3.5 w-3.5 animate-spin" /></span>
              <p className="min-w-0 flex-1 truncate text-sm">{j.title}</p><span className="w-16 text-right text-sm tabular-nums">{j.reserved_gb.toFixed(1)} GB</span>
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}

/** Compact pill for headers: used/total, click to open the panel. */
export function MemoryPill() {
  const L = useL();
  const { mem } = useMemory();
  const [open, setOpen] = useState(false);
  if (!mem) return null;
  const loading = mem.instances.some((i: any) => i.state === "loading");
  return (
    <>
      <button onClick={() => setOpen(true)} className="flex shrink-0 items-center gap-2 rounded-lg px-2 py-1.5 text-xs text-muted hover:bg-elevated" aria-label={L("Memoria del Spark", "Spark memory")}>
        {loading ? <Loader2 className="h-3.5 w-3.5 animate-spin text-info" /> : <MemoryStick className="h-3.5 w-3.5" />}
        <MemoryBar mem={mem} className="h-2 w-20" />
        <span className="hidden whitespace-nowrap tabular-nums sm:inline">{mem.reserved_gb.toFixed(0)}/{mem.schedulable_gb.toFixed(0)} GB</span>
      </button>
      <Modal open={open} onOpenChange={setOpen} title={L("Memoria del Spark", "Spark memory")} wide><MemoryPanel /></Modal>
    </>
  );
}

/** Ask before loading a model that is not resident: memory it needs, models that would be unloaded, ETA. */
export function LoadConfirm({ plan, onConfirm, onCancel, onUseDefault, defaultName }: { plan: any; onConfirm: () => void; onCancel: () => void; onUseDefault?: () => void; defaultName?: string }) {
  const L = useL();
  if (!plan) return null;
  const eta = plan.expected_load_seconds ? Math.ceil(plan.expected_load_seconds / 60) : null;
  return (
    <Modal open onOpenChange={(o) => { if (!o) onCancel(); }} title={L(`Cargar ${plan.model}`, `Load ${plan.model}`)}
      description={L("Este modelo no está en memoria. La memoria del Spark es compartida por todos.", "This model is not in memory. The Spark memory is shared by everyone.")}>
      <div className="space-y-3 text-sm">
        <p>{L("Necesita", "Needs")} <b>{plan.need_gb.toFixed(0)} GB</b> · {L("libres ahora", "free now")} <b>{plan.free_gb.toFixed(0)} GB</b>{eta ? <> · {L("carga en", "loads in")} ≈ <b>{eta} min</b></> : null}</p>
        {plan.fits && plan.evict.length > 0 && <Notice tone="warn">{L("Para hacer sitio se descargarán modelos que nadie está usando:", "To make room these idle models will be unloaded:")} {plan.evict.map((e: any) => `${e.name} (${e.reserved_gb.toFixed(0)} GB)`).join(", ")}.</Notice>}
        {!plan.fits && <Notice tone="danger">{L("Ahora mismo no cabe: hay modelos en uso o fijados.", "It does not fit right now: models are in use or pinned.")} {plan.blocked_by?.map((b: any) => `${b.name} (${Number(b.reserved_gb ?? 0).toFixed(0)} GB${b.in_use ? L(", en uso", ", in use") : b.protected ? L(", fijado", ", pinned") : ""})`).join(", ")}</Notice>}
        {plan.fits && !plan.evict.length && <Notice tone="ok">{L("Cabe sin descargar nada.", "It fits without unloading anything.")}</Notice>}
        <p className="text-xs text-muted">{L(`Si nadie lo usa durante unos minutos se descargará solo.`, "If nobody uses it for a few minutes it unloads by itself.")}</p>
        <div className="flex flex-wrap justify-end gap-2">
          <Button variant="ghost" onClick={onCancel}>{L("Cancelar", "Cancel")}</Button>
          {onUseDefault && defaultName && <Button onClick={onUseDefault}>{L(`Usar ${defaultName} (ya cargado)`, `Use ${defaultName} (loaded)`)}</Button>}
          <Button variant="primary" disabled={!plan.fits} onClick={onConfirm}>{L("Cargar y continuar", "Load and continue")}</Button>
        </div>
      </div>
    </Modal>
  );
}
