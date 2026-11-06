"use client";
import { CancelLoadButton } from "@/components/cancel-load";
import { AlertTriangle, Crown, Download, Loader2, Play, Power, RefreshCw, Search, ShieldAlert, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { kTok } from "@/components/assistant";
import { Guide } from "@/components/guide";
import { LoadConfirm, mmss, useMemory, useTick } from "@/components/memory";
import { Badge, Button, Card, CardBody, CardHeader, Confirm, ErrorBox, Input, Modal, Notice, Progress, Skeleton, StatusBadge, Switch, Tabs, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { bytes, compact, duration, num, params, rate } from "@/lib/format";
import { useL } from "@/lib/i18n";
import { isManager, useMe, useToast } from "@/lib/providers";

const LLM = ["ollama", "vllm"];
const SWITCHABLE = ["installed", "ready", "loading", "busy", "unloading", "failed"];
const isChatLlm = (m: any) => LLM.includes(m?.backend) && (m?.capabilities ?? []).includes("chat") && m?.modality !== "embedding";

/** Current main model, last switch and pending download-to-main. Polls faster while something is changing. */
export function useMain() {
  const { data, mutate } = useSWR("/api/v1/models/main", {
    refreshInterval: (d: any) => (d?.pending || ["loading", "waiting"].includes(d?.switch?.state) || ["loading", "unloading"].includes(d?.model?.state) ? 3000 : 10000),
  });
  return { main: data, refreshMain: mutate };
}

function memText(m: any, inst: any, L: (es: string, en: string) => string) {
  if (inst?.measured_gb) return `${num(inst.measured_gb)} GB ${L("medidos", "measured")}`;
  if (inst?.reserved_gb) return `${num(inst.reserved_gb)} GB ${L("reservados", "reserved")}`;
  if (m?.measured_memory_gb) return `${num(m.measured_memory_gb)} GB ${L("medidos", "measured")}`;
  if (m?.estimated_memory_gb) return `~${num(m.estimated_memory_gb)} GB ${L("estimados", "estimated")}`;
  return "—";
}

/** Loading progress with ETA (from the last successful load of the model). */
function LoadingLine({ inst, L }: { inst: any; L: (es: string, en: string) => string }) {
  useTick(inst?.started_at);
  const elapsed = inst?.started_at ? (Date.now() - new Date(inst.started_at).getTime()) / 1000 : 0;
  const eta = inst?.expected_load_seconds;
  return (
    <div className="flex flex-wrap items-center gap-2 text-[11px] text-info">
      <Loader2 className="h-3 w-3 animate-spin" />{L("Cargando", "Loading")} {mmss(elapsed)}
      {eta ? <><Progress className="w-28" value={Math.min(97, (elapsed / eta) * 100)} tone="accent" /><span className="text-muted">≈ {mmss(Math.max(0, eta - elapsed))} {L("restantes", "left")}</span></> : <span className="text-muted">{L("(la primera carga de un modelo grande tarda varios minutos)", "(the first load of a large model takes several minutes)")}</span>}
    </div>
  );
}

/** Explains what making `model` main does (memory plan) and confirms. */
export function MakeMainDialog({ model, onClose, onDone }: { model: any; onClose: () => void; onDone?: () => void }) {
  const L = useL();
  const { push } = useToast();
  const { data: plan, error } = useSWR(model ? `/api/v1/models/main/plan?model_id=${model.id}` : null, { revalidateOnFocus: false });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<any>(null);
  if (!model) return null;
  const eta = plan?.expected_load_seconds ? Math.ceil(plan.expected_load_seconds / 60) : null;
  const others = (plan?.evict ?? []).filter((e: any) => !e.previous_main);
  async function go(force = false) {
    setBusy(true); setErr(null);
    try {
      const r = await api("/api/v1/models/main", { method: "POST", json: { model_id: model.id, force } });
      push("ok", L(`${r.alias} es ahora el modelo principal${plan?.resident ? "" : ": cargando en segundo plano"}`, `${r.alias} is now the main model${plan?.resident ? "" : ": loading in the background"}`));
      onDone?.(); onClose();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  }
  return (
    <Modal open onOpenChange={(o) => { if (!o) onClose(); }} title={L(`Hacer principal: ${model.alias}`, `Make main: ${model.alias}`)}
      description={L("El modelo principal está siempre cargado para todos y es el que usan por defecto el chat, la API y las herramientas.", "The main model is always loaded for everyone and is the default for chat, API and tools.")}>
      {error && <ErrorBox error={error} />}
      {!plan && !error && <Skeleton className="h-32" />}
      {plan && (
        <div className="space-y-3 text-sm">
          <ol className="list-decimal space-y-1.5 pl-5">
            <li>{L(`${model.alias} queda fijado en memoria y pasa a ser el modelo por defecto (chat, API, herramientas que apuntaban al anterior). Se hace visible para todos.`, `${model.alias} is pinned in memory and becomes the default model (chat, API, tools that pointed at the previous one). It becomes visible to everyone.`)}</li>
            <li>{plan.resident ? L("Ya está cargado: el cambio es inmediato, sin espera.", "It is already loaded: the switch is immediate, no downtime.")
              : L(`Se carga en segundo plano${eta ? ` (≈ ${eta} min)` : " (varios minutos la primera vez)"}. Mientras tanto, quien use el modelo por defecto esperará a que esté listo; quien elija otro modelo no se ve afectado.`, `It loads in the background${eta ? ` (≈ ${eta} min)` : " (several minutes the first time)"}. Meanwhile requests to the default model wait until it is ready; people choosing another model are not affected.`)}</li>
            {plan.previous_alias && <li>{plan.unload_previous
              ? L(`${plan.previous_alias} (el principal actual) se descargará de memoria para dejar sitio (${num(plan.previous?.reserved_gb)} GB)${plan.previous?.in_use ? ", en cuanto termine de responder" : ""}.`, `${plan.previous_alias} (the current main) is unloaded to make room (${num(plan.previous?.reserved_gb)} GB)${plan.previous?.in_use ? ", as soon as it finishes answering" : ""}.`)
              : L(`${plan.previous_alias} deja de estar fijado: queda como un modelo normal y se descargará tras ${plan.idle_timeout_min} min sin uso o cuando haga falta memoria.`, `${plan.previous_alias} is unpinned: it becomes a normal model and unloads after ${plan.idle_timeout_min} idle min or when memory is needed.`)}</li>}
          </ol>
          {!plan.resident && <p className="text-xs text-muted">{L("Necesita", "Needs")} <b className="text-fg">{num(plan.need_gb, 0)} GB</b> · {L("libres ahora", "free now")} <b className="text-fg">{num(plan.free_gb, 0)} GB</b> {L("de", "of")} {num(plan.schedulable_gb, 0)} GB</p>}
          {plan.fits && others.length > 0 && <Notice tone="warn">{L("También se descargarán modelos inactivos:", "Idle models will also be unloaded:")} {others.map((e: any) => `${e.name} (${num(e.reserved_gb, 0)} GB)`).join(", ")}.</Notice>}
          {!plan.fits && <Notice tone="danger">{L("Ahora mismo no cabe en memoria", "It does not fit in memory right now")}{plan.reason ? ` (${plan.reason})` : ""}. {plan.blocked_by?.length ? <>{L("Ocupan la memoria:", "Memory held by:")} {plan.blocked_by.map((b: any) => `${b.name} (${num(b.reserved_gb, 0)} GB${b.in_use ? L(", en uso", ", in use") : b.protected ? L(", fijado", ", pinned") : ""})`).join(", ")}. </> : null}
            {plan.need_gb <= plan.schedulable_gb && L("Puedes hacerlo principal igualmente: se cargará solo cuando se libere memoria (se reintenta cada 10 min).", "You can make it main anyway: it loads by itself when memory frees up (retried every 10 min).")}</Notice>}
          {err && <ErrorBox error={err} />}
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="ghost" onClick={onClose}>{L("Cancelar", "Cancel")}</Button>
            {plan.fits ? <Button variant="primary" loading={busy} onClick={() => go(false)}><Crown className="h-4 w-4" />{L("Hacer principal", "Make main")}</Button>
              : plan.need_gb <= plan.schedulable_gb && <Button variant="danger" loading={busy} onClick={() => go(true)}>{L("Hacer principal igualmente", "Make main anyway")}</Button>}
          </div>
        </div>
      )}
    </Modal>
  );
}

/** Load / Unload / Make main for one model, with live state, load progress + ETA, memory and the reason when disabled. */
export function ModelLoadButtons({ m, mainAlias, onChanged, size = "sm", vertical }: { m: any; mainAlias?: string | null; onChanged?: () => void; size?: "sm" | "md"; vertical?: boolean }) {
  const L = useL();
  const { me } = useMe();
  const { push } = useToast();
  const { mem, refresh } = useMemory();
  const [plan, setPlan] = useState<any>(null);
  const [busy, setBusy] = useState("");
  const [askUnload, setAskUnload] = useState(false);
  const [makeMain, setMakeMain] = useState(false);
  if (!LLM.includes(m.backend)) return null;
  const inst = mem?.instances?.find((i: any) => i.model_id === m.id);
  const state: string | null = inst?.state ?? m.runtime?.state ?? null;
  const loaded = state === "ready" || state === "busy";
  const canUse = !!me && me.role !== "viewer";
  const manager = isManager(me);
  const isMain = !!mainAlias && mainAlias === m.alias;
  const done = () => { refresh(); onChanged?.(); };
  async function load() {
    setBusy("load");
    try {
      const pl = await api(`/api/v1/models/${m.id}/load-plan`);
      if (pl.resident) { done(); return; }
      setPlan({ ...pl, id: m.id });
    } catch (e: any) { push("error", e.message); } finally { setBusy(""); }
  }
  async function unload() {
    setBusy("unload"); setAskUnload(false);
    try { await api(`/api/v1/models/${m.id}/unload`, { method: "POST" }); push("ok", L(`${m.alias} descargado de memoria`, `${m.alias} unloaded`)); done(); } catch (e: any) { push("error", e.message); } finally { setBusy(""); }
  }
  let reason = "";
  let button: React.ReactNode = null;
  if (state === "loading") button = <span className="flex flex-wrap items-center gap-2"><LoadingLine inst={inst} L={L} />
    {m.backend === "vllm" && (!m.pinned || manager) && <CancelLoadButton modelId={m.id} alias={m.alias} onDone={done} />}</span>;
  else if (state === "unloading") button = <span className="flex items-center gap-1 text-[11px] text-warn"><Loader2 className="h-3 w-3 animate-spin" />{L("Descargando de memoria…", "Unloading…")}</span>;
  else if (loaded) {
    if (!canUse) reason = L("Necesitas rol investigador para descargarlo", "You need the researcher role to unload it");
    else if (m.pinned && !manager) reason = L("Es el modelo general: solo administradores/gestores lo descargan", "It is the general model: only administrators/managers unload it");
    else if (inst?.active_requests > 0) reason = L("Está respondiendo ahora; se puede descargar cuando termine", "Answering now; it can be unloaded when it finishes");
    button = <Button size={size} variant="outline" disabled={!!reason} title={reason || undefined} loading={busy === "unload"} onClick={() => (m.pinned || isMain ? setAskUnload(true) : unload())}><Power className="h-3.5 w-3.5" />{L("Descargar", "Unload")}</Button>;
  } else {
    const st = m.status;
    if (!canUse) reason = L("Necesitas rol investigador para cargarlo", "You need the researcher role to load it");
    else if (["discovered", "downloading", "downloaded", "installing"].includes(st)) reason = L("Aún se está instalando", "Still installing");
    else if (st === "disabled") reason = L("Deshabilitado", "Disabled");
    else if (st === "removing") reason = L("Se está eliminando", "Being removed");
    button = <Button size={size} variant={size === "md" ? "primary" : "outline"} disabled={!!reason} title={reason || undefined} loading={busy === "load"} onClick={load}><Play className="h-3.5 w-3.5" />{L("Cargar", "Load")}</Button>;
  }
  return (
    <div className={cx("flex gap-1.5", vertical ? "flex-col items-start" : "flex-wrap items-center")}>
      {button}
      {loaded && inst && <span className="text-[11px] tabular-nums text-muted">{num(inst.reserved_gb)} GB</span>}
      {isMain ? <Badge tone="accent"><Crown className="h-3 w-3" />{L("Principal", "Main")}</Badge>
        : manager && isChatLlm(m) && SWITCHABLE.includes(m.status) && <Button size={size} variant="ghost" onClick={() => setMakeMain(true)}><Crown className="h-3.5 w-3.5" />{L("Hacer principal", "Make main")}</Button>}
      {reason && <span className="max-w-[220px] text-[11px] text-muted">{reason}</span>}
      {plan && <LoadConfirm plan={plan} onCancel={() => setPlan(null)} onConfirm={async () => {
        const pl = plan; setPlan(null);
        try { await api(`/api/v1/models/${pl.id}/load?background=true`, { method: "POST" }); push("ok", L(`${pl.model}: cargando…`, `${pl.model}: loading…`)); done(); } catch (e: any) { push("error", e.message); }
      }} />}
      <Confirm open={askUnload} onOpenChange={setAskUnload} title={L(`Descargar ${m.alias}`, `Unload ${m.alias}`)} onConfirm={unload}
        text={L("Es el modelo principal: mientras no esté en memoria, los chats y herramientas que lo usan esperarán a que se vuelva a cargar. El sistema lo recarga solo en unos minutos (salvo en modo «bajo demanda»). Para cambiarlo por otro usa «Hacer principal».", "It is the main model: while it is not in memory, chats and tools using it wait until it is loaded again. The system reloads it by itself in a few minutes (except in on-demand mode). To replace it use “Make main”.")} />
      {makeMain && <MakeMainDialog model={m} onClose={() => setMakeMain(false)} onDone={done} />}
    </div>
  );
}

/** Plan + "Download and make main" for a Hugging Face repository. */
function HfCandidate({ repo, onDone }: { repo: string; onDone: () => void }) {
  const L = useL();
  const { push } = useToast();
  const { data: plan, error } = useSWR(`hfplan:${repo}`, () => api("/api/v1/huggingface/plan", { method: "POST", json: { repo_id: repo } }), { revalidateOnFocus: false });
  const [accept, setAccept] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<any>(null);
  if (error) return <ErrorBox error={error} />;
  if (!plan) return <div className="space-y-2"><Skeleton className="h-24" /><p className="text-xs text-muted">{L("Analizando el repositorio (formatos, motor, memoria, disco)…", "Analysing the repository (formats, engine, memory, disk)…")}</p></div>;
  const licWarn = plan.warnings?.some((w: any) => w.code === "license");
  const notLlm = !LLM.includes(plan.engine) || !["text-generation", "image-text-to-text", null, undefined].includes(plan.pipeline_tag);
  const est = plan.engine_analysis?.estimate?.total_gb;
  const blocked = plan.blockers.length > 0 || notLlm || (licWarn && !accept);
  async function go() {
    setBusy(true); setErr(null);
    try {
      const r = await api("/api/v1/models/main", { method: "POST", json: { install: { repo_id: repo, revision: plan.revision, engine: plan.engine, accept_license: accept, visibility: "public" } } });
      push("ok", r.pending ? L(`Descargando ${r.alias}: será el principal al terminar la instalación`, `Downloading ${r.alias}: it becomes main when the install finishes`) : L(`${r.alias} es ahora el modelo principal`, `${r.alias} is now the main model`));
      onDone();
    } catch (e) { setErr(e); } finally { setBusy(false); }
  }
  return (
    <div className="space-y-3 text-sm">
      <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{repo}</span><StatusBadge state={plan.verdict} />{plan.engine && <Badge>{plan.engine}</Badge>}{plan.analysis?.quantization && <Badge tone="accent">{plan.analysis.quantization}</Badge>}
        <Link href={`/discover/${repo}`} className="ml-auto text-xs text-muted hover:text-accent">{L("Ficha completa", "Full details")} →</Link></div>
      <div className="grid gap-2 text-xs sm:grid-cols-3">
        <div className="rounded-lg bg-elevated/70 p-2"><div className="text-muted">{L("Descarga", "Download")}</div><div className="font-semibold tabular-nums">{bytes(plan.download_bytes)}</div></div>
        <div className="rounded-lg bg-elevated/70 p-2"><div className="text-muted">{L("Memoria estimada", "Estimated memory")}</div><div className="font-semibold tabular-nums">{est ? `${num(est)} GB` : "—"}</div></div>
        <div className="rounded-lg bg-elevated/70 p-2"><div className="text-muted">{L("Veredicto", "Verdict")}</div><div className="font-semibold"><StatusBadge state={plan.verdict} /></div></div>
      </div>
      {notLlm && <Notice tone="danger">{L("Este repositorio no es un modelo de lenguaje servible por vLLM/Ollama: no puede ser el principal.", "This repository is not a language model served by vLLM/Ollama: it cannot be the main model.")}</Notice>}
      {plan.blockers.map((b: string, i: number) => <Notice key={i} tone="danger" icon={<AlertTriangle className="h-4 w-4" />}>{b}</Notice>)}
      {plan.warnings.map((w: any, i: number) => <Notice key={i} tone={w.level === "danger" ? "danger" : w.level === "warning" ? "warn" : "info"} icon={<ShieldAlert className="h-4 w-4" />}>{w.message}</Notice>)}
      <ol className="list-decimal space-y-1 pl-5 text-xs text-muted">
        <li>{L(`Se descarga (${bytes(plan.download_bytes)}) y se verifica (sha256). Un modelo grande puede tardar horas: el progreso se ve aquí y en «Descargas».`, `It is downloaded (${bytes(plan.download_bytes)}) and verified (sha256). A large model can take hours: progress shows here and in “Downloads”.`)}</li>
        <li>{L("Mientras tanto el modelo principal actual sigue funcionando con normalidad.", "Meanwhile the current main model keeps working normally.")}</li>
        <li>{L("Al terminar la instalación pasa a ser el principal automáticamente y se carga (unos minutos); el anterior queda como modelo normal y se descarga si hace falta memoria.", "When the install finishes it becomes main automatically and loads (a few minutes); the previous one becomes a normal model, unloaded if memory is needed.")}</li>
      </ol>
      {licWarn && <Switch checked={accept} onChange={setAccept} label={L(`He revisado y acepto la licencia (${plan.license ?? "?"})`, `I reviewed and accept the licence (${plan.license ?? "?"})`)} />}
      {err && <ErrorBox error={err} />}
      <div className="flex justify-end"><Button variant="primary" disabled={blocked} loading={busy} onClick={go}><Download className="h-4 w-4" />{L("Descargar y hacer principal", "Download and make main")}</Button></div>
    </div>
  );
}

/** Picker: downloaded LLMs, recommended downloads (compatibility verdict + size) and a Hugging Face search. */
function MainPicker({ open, onOpenChange, mainAlias, onDone }: { open: boolean; onOpenChange: (o: boolean) => void; mainAlias?: string | null; onDone: () => void }) {
  const L = useL();
  const { data: models } = useSWR(open ? "/api/v1/models" : null);
  const { data: rec, error: recErr } = useSWR(open ? "rec:main-model" : null, () => api("/api/v1/recommendations", { method: "POST", json: { task: "text-generation", limit: 10 } }), { revalidateOnFocus: false });
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const { data: found, error: findErr, isLoading: finding } = useSWR(open && query ? `/api/v1/huggingface/search?q=${encodeURIComponent(query)}&task=text-generation&limit=20` : null, { revalidateOnFocus: false });
  const [local, setLocal] = useState<any>(null);
  const [repo, setRepo] = useState<string | null>(null);
  const llms = (models ?? []).filter(isChatLlm);
  const byRepo = (rid: string) => llms.find((m: any) => m.repo_id === rid && SWITCHABLE.includes(m.status));
  const pick = (rid: string) => { const m = byRepo(rid); if (m) setLocal(m); else setRepo(rid); };
  const close = () => { setRepo(null); onOpenChange(false); };
  const row = (key: string, main: React.ReactNode, sub: React.ReactNode, action: React.ReactNode) => (
    <li key={key} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5"><div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-1.5 text-sm font-medium">{main}</div><div className="text-[11px] text-muted">{sub}</div></div>{action}</li>
  );
  return (
    <Modal open={open} onOpenChange={(o) => (o ? onOpenChange(o) : close())} wide title={L("Elegir el modelo principal", "Choose the main model")}
      description={L("Entre los modelos ya descargados o uno nuevo de Hugging Face. Solo modelos de lenguaje (chat).", "Among the downloaded models or a new one from Hugging Face. Language (chat) models only.")}>
      <Guide id="main_model" />
      {repo ? (
        <div className="space-y-3"><Button variant="ghost" size="sm" onClick={() => setRepo(null)}>← {L("Volver a la lista", "Back to the list")}</Button><HfCandidate repo={repo} onDone={() => { onDone(); close(); }} /></div>
      ) : (
        <Tabs tabs={[
          { id: "local", label: L("Descargados", "Downloaded"), content: !models ? <Skeleton className="h-32" /> : llms.length === 0 ? <Notice tone="info">{L("No hay modelos de lenguaje instalados: elige uno en «Recomendados» o búscalo.", "No language models installed: choose one in “Recommended” or search.")}</Notice> : (
            <ul className="divide-y divide-border/60">{llms.map((m: any) => row(m.id,
              <>{m.alias}{m.alias === mainAlias && <Badge tone="accent"><Crown className="h-3 w-3" />{L("actual", "current")}</Badge>}<StatusBadge state={m.status} />{m.runtime && <Badge tone="ok">{L("en memoria", "in memory")}</Badge>}</>,
              <>{m.repo_id ?? m.engine_ref} · {m.backend}{m.parameters_b ? ` · ${params(m.parameters_b)}` : ""}{m.quantization ? ` · ${m.quantization}` : ""}{m.context_window ? ` · ${L("contexto", "context")} ${kTok(m.context_window)}` : ""} · {memText(m, null, L)}</>,
              m.alias === mainAlias ? null : <Button size="sm" variant="outline" disabled={!SWITCHABLE.includes(m.status)} title={!SWITCHABLE.includes(m.status) ? L("Aún no está instalado", "Not installed yet") : undefined} onClick={() => setLocal(m)}><Crown className="h-3.5 w-3.5" />{L("Elegir", "Choose")}</Button>))}</ul>) },
          { id: "rec", label: <span className="flex items-center gap-1"><Sparkles className="h-3.5 w-3.5" />{L("Recomendados", "Recommended")}</span>, content: recErr ? <ErrorBox error={recErr} /> : !rec ? <div className="space-y-2"><Skeleton className="h-32" /><p className="text-xs text-muted">{L("Comprobando compatibilidad con el Spark (puede tardar unos segundos)…", "Checking compatibility with the Spark (may take a few seconds)…")}</p></div> : (
            <ul className="divide-y divide-border/60">{rec.results.map((r: any) => row(r.repo_id,
              <>{r.repo_id}<StatusBadge state={r.verdict} /><Badge>{r.engine}</Badge>{r.source === "nvidia_spark_matrix" && <Badge tone="ok">NVIDIA Spark</Badge>}{byRepo(r.repo_id) && <Badge tone="info">{L("descargado", "downloaded")}</Badge>}</>,
              <>{L("memoria", "memory")} ~{num(r.estimated_memory_gb)} GB · {L("disco", "disk")} {num(r.disk_gb)} GB{r.parameters_b ? ` · ${r.parameters_b}B` : ""}{r.quantization ? ` · ${r.quantization}` : ""}{r.license ? ` · ${r.license}` : ""}</>,
              <Button size="sm" variant="outline" onClick={() => pick(r.repo_id)}>{byRepo(r.repo_id) ? <><Crown className="h-3.5 w-3.5" />{L("Elegir", "Choose")}</> : <><Download className="h-3.5 w-3.5" />{L("Ver y descargar", "Review & download")}</>}</Button>))}
              {rec.results.length === 0 && <li className="py-3 text-sm text-muted">{L("Sin recomendaciones ahora mismo; usa la búsqueda.", "No recommendations right now; use search.")}</li>}</ul>) },
          { id: "search", label: <span className="flex items-center gap-1"><Search className="h-3.5 w-3.5" />Hugging Face</span>, content: (
            <div className="space-y-3">
              <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setQuery(q.trim()); }}>
                <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder={L("Nombre, organización/repo o enlace de huggingface.co", "Name, owner/repo or huggingface.co link")} aria-label="search" />
                <Button type="submit" variant="primary">{L("Buscar", "Search")}</Button>
              </form>
              {findErr && <ErrorBox error={findErr} />}
              {finding && <Skeleton className="h-24" />}
              {found && <ul className="divide-y divide-border/60">{found.map((r: any) => row(r.id,
                <>{r.id}{r.quantization && <Badge tone="accent">{r.quantization}</Badge>}{r.gated && <Badge tone="warn">gated</Badge>}{byRepo(r.id) && <Badge tone="info">{L("descargado", "downloaded")}</Badge>}</>,
                <>{r.pipeline_tag ?? ""}{r.parameters ? ` · ${num(r.parameters / 1e9, 1)}B` : ""} · {compact(r.downloads)} {L("descargas", "downloads")}</>,
                <Button size="sm" variant="outline" onClick={() => pick(r.id)}>{byRepo(r.id) ? L("Elegir", "Choose") : L("Ver compatibilidad", "Check compatibility")}</Button>))}
                {found.length === 0 && <li className="py-3 text-sm text-muted">{L("Sin resultados", "No results")}</li>}</ul>}
              <p className="text-[11px] text-muted">{L("Al elegir un repositorio verás si cabe en el Spark (veredicto, memoria y disco) antes de descargar nada.", "When you pick a repository you see whether it fits the Spark (verdict, memory and disk) before anything is downloaded.")}</p>
            </div>) },
        ]} />
      )}
      {local && <MakeMainDialog model={local} onClose={() => setLocal(null)} onDone={() => { onDone(); close(); }} />}
    </Modal>
  );
}

/** "Main model" card at the top of the library: current main, its state and memory, switch progress, pending download. */
export function MainModelCard() {
  const L = useL();
  const { me } = useMe();
  const { push } = useToast();
  const { main, refreshMain } = useMain();
  const { mem, refresh } = useMemory();
  const [picker, setPicker] = useState(false);
  if (!main) return <Skeleton className="mb-5 h-28" />;
  const m = main.model;
  const inst = m ? mem?.instances?.find((i: any) => i.model_id === m.id) : null;
  const sw = main.switch;
  const pend = main.pending;
  const manager = isManager(me);
  const done = () => { refreshMain(); refresh(); };
  const d = pend?.download;
  const left = d?.speed_bps && d.total_bytes ? (d.total_bytes - d.downloaded_bytes) / d.speed_bps : null;
  return (
    <Card className="mb-5">
      <CardHeader icon={<Crown className="h-4 w-4" />} title={L("Modelo principal", "Main model")}
        subtitle={L("El modelo de lenguaje siempre cargado para todos; el chat, la API y las herramientas lo usan por defecto.", "The language model always loaded for everyone; chat, API and tools use it by default.")}
        actions={manager ? <Button size="sm" variant="primary" onClick={() => setPicker(true)}><RefreshCw className="h-3.5 w-3.5" />{m ? L("Cambiar", "Change") : L("Elegir", "Choose")}</Button>
          : <span className="text-[11px] text-muted">{L("Solo administradores y gestores pueden cambiarlo", "Only administrators and managers can change it")}</span>} />
      <CardBody className="space-y-3">
        {!m ? <Notice tone="warn">{main.alias ? L(`El modelo principal configurado («${main.alias}») ya no existe.`, `The configured main model (“${main.alias}”) no longer exists.`) : L("No hay modelo principal: elige uno para que el chat responda al instante.", "There is no main model: choose one so chat answers instantly.")}</Notice> : (
          <div className="flex flex-wrap items-start gap-x-6 gap-y-3">
            <div className="min-w-0 flex-1">
              <Link href={`/models/${m.id}`} className="text-base font-semibold hover:text-accent">{m.alias}</Link>
              <p className="text-[11px] text-muted">{m.repo_id ?? m.engine_ref}{m.parameters_b ? ` · ${params(m.parameters_b)}` : ""}{m.quantization ? ` · ${m.quantization}` : ""}</p>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                <Badge>{m.backend}</Badge>
                {m.context_window && <Badge tone="info">{L("contexto", "context")} {kTok(m.context_window)}</Badge>}
                {m.state === "loaded" && <Badge tone="ok">{L("Cargado · responde al instante", "Loaded · answers instantly")}</Badge>}
                {m.state === "stopped" && <Badge tone="warn">{L("Detenido", "Stopped")}</Badge>}
                {(m.capabilities ?? []).filter((c: string) => ["tools", "vision", "reasoning"].includes(c)).map((c: string) => <Badge key={c}>{c}</Badge>)}
              </div>
              {m.state === "stopped" && !["loading", "waiting"].includes(sw?.state) && <p className="mt-1.5 text-[11px] text-muted">{main.mode === "on_demand" ? L("Modo «bajo demanda»: se carga con la primera petición.", "On-demand mode: it loads with the first request.") : L("El sistema lo vuelve a cargar solo en unos minutos; o pulsa «Cargar».", "The system reloads it by itself in a few minutes; or click “Load”.")}</p>}
            </div>
            <div className="text-right text-xs"><div className="text-muted">{L("Memoria", "Memory")}</div><div className="text-sm font-semibold tabular-nums">{memText(m, inst ?? (m.reserved_gb ? { reserved_gb: m.reserved_gb, measured_gb: m.measured_gb } : null), L)}</div></div>
            <ModelLoadButtons m={m} mainAlias={main.alias} onChanged={done} vertical />
          </div>
        )}
        {sw && sw.alias === m?.alias && ["loading", "waiting"].includes(sw.state) && <Notice tone="info">{sw.state === "waiting" ? L(`Esperando a que ${sw.previous} termine de responder para liberar su memoria y cargar ${sw.alias}…`, `Waiting for ${sw.previous} to finish answering to free its memory and load ${sw.alias}…`) : L(`Cambiando a ${sw.alias}: cargando en segundo plano.${sw.previous ? ` ${sw.previous} queda como modelo normal.` : ""}`, `Switching to ${sw.alias}: loading in the background.${sw.previous ? ` ${sw.previous} becomes a normal model.` : ""}`)}</Notice>}
        {sw && sw.alias === m?.alias && sw.state === "failed" && <Notice tone="danger">{L(`No se pudo cargar ${sw.alias}: `, `Could not load ${sw.alias}: `)}{sw.error} {L("Se reintentará automáticamente en 10 min.", "It is retried automatically in 10 min.")}
          {manager && <Button size="sm" variant="outline" className="ml-2" onClick={async () => { try { await api("/api/v1/models/main", { method: "POST", json: { model_id: m.id, force: true } }); push("ok", L("Reintentando…", "Retrying…")); done(); } catch (e: any) { push("error", e.message); } }}>{L("Reintentar ahora", "Retry now")}</Button>}</Notice>}
        {pend && (
          <div className={cx("rounded-xl border p-3 text-sm", pend.error ? "border-danger/40 bg-danger/5" : "border-accent/30 bg-accent/[0.04]")}>
            <div className="flex flex-wrap items-center gap-2">
              {pend.error ? <AlertTriangle className="h-4 w-4 text-danger" /> : <Loader2 className="h-4 w-4 animate-spin text-accent" />}
              <span className="font-medium">{pend.error ? L(`No se pudo instalar ${pend.alias}`, `Could not install ${pend.alias}`) : L(`Descargando ${pend.alias} para hacerlo principal`, `Downloading ${pend.alias} to make it main`)}</span>
              <StatusBadge state={pend.status} />
              {manager && <Button size="sm" variant="ghost" className="ml-auto" onClick={async () => { try { await api("/api/v1/models/main/pending", { method: "DELETE" }); push("ok", L("Ya no se hará principal al terminar (la descarga sigue en «Descargas»)", "It will no longer become main (the download continues in “Downloads”)")); done(); } catch (e: any) { push("error", e.message); } }}><X className="h-3.5 w-3.5" />{pend.error ? L("Cerrar", "Dismiss") : L("No hacerlo principal", "Don't make it main")}</Button>}
            </div>
            {pend.error ? <p className="mt-1 text-xs text-danger">{pend.error}. {L("Reintenta la descarga en la pestaña «Descargas» (se hará principal al terminar) o elige otro modelo.", "Retry the download in the “Downloads” tab (it becomes main when done) or choose another model.")}</p> : d ? (
              <div className="mt-2 flex items-center gap-3">
                <Progress value={d.total_bytes ? (d.downloaded_bytes / d.total_bytes) * 100 : 0} tone="accent" />
                <span className="shrink-0 text-right text-xs tabular-nums text-muted">{bytes(d.downloaded_bytes)} / {bytes(d.total_bytes)}{d.status === "running" && d.speed_bps ? ` · ${rate(d.speed_bps)}` : ""}{left ? ` · ≈ ${duration(left)}` : ""}{d.status === "verifying" ? ` · ${L("verificando", "verifying")}` : ""}{d.status === "paused" ? ` · ${L("en pausa", "paused")}` : ""}</span>
              </div>) : null}
            {!pend.error && <p className="mt-1.5 text-[11px] text-muted">{L("El modelo principal actual sigue funcionando. Al terminar la instalación el cambio es automático.", "The current main model keeps working. When the install finishes the switch is automatic.")}</p>}
          </div>
        )}
      </CardBody>
      {picker && <MainPicker open={picker} onOpenChange={setPicker} mainAlias={main.alias} onDone={done} />}
    </Card>
  );
}
