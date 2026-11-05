"use client";
import { AlertTriangle, ArrowLeft, Download, FileText, Package, ShieldAlert } from "lucide-react";
import Link from "next/link";
import { use, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR from "swr";
import { Badge, Button, Card, CardBody, CardHeader, ErrorBox, Field, Input, Modal, Notice, PageHeader, Select, Skeleton, StatusBadge, Switch, Table, Tabs, Td, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { bytes, compact, dt, num } from "@/lib/format";
import { useI18n, tr } from "@/lib/i18n";
import { useMe, useToast } from "@/lib/providers";

function Verdicts({ analysis }: { analysis: any }) {
  const { t } = useI18n();
  if (!analysis) return null;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2 text-sm"><span className="text-muted">{t("discover.analysis")}:</span><StatusBadge state={analysis.overall} />
        {analysis.quantization && <Badge tone="accent">{analysis.quantization}</Badge>}
        {analysis.architectures?.map((a: string) => <Badge key={a}>{a}</Badge>)}
        <span className="text-xs text-muted">{num(analysis.disk_gb_total)} GB en el repositorio</span></div>
      {analysis.reasons?.map((r: string, i: number) => <Notice key={i} tone="danger">{r}</Notice>)}
      {analysis.engines?.length === 0 && <Notice tone="warn">{tr("No hay adaptador de motor para este repositorio.", "No engine adapter for this repository.")}</Notice>}
      {analysis.engines?.map((e: any) => (
        <div key={e.engine} className="rounded-xl border border-border p-4">
          <div className="flex flex-wrap items-center gap-2"><span className="font-medium">{e.engine}</span><StatusBadge state={e.verdict} /><span className="text-xs text-muted">{t("discover.confidence")} {Math.round(e.confidence * 100)} %</span></div>
          <div className="mt-3 grid gap-2 text-xs sm:grid-cols-4">
            {[["weights_gb", "pesos"], ["kv_cache_gb", "KV cache"], ["overhead_gb", "overhead"], ["total_gb", "total"]].map(([k, l]) => (
              <div key={k} className="rounded-lg bg-elevated/70 p-2"><div className="text-muted">{l}</div><div className="font-semibold text-fg tabular-nums">{num(e.estimate?.[k])} GB</div></div>
            ))}
          </div>
          <p className="mt-2 text-[11px] text-subtle">{t("common.estimated")}: {e.estimate?.basis?.join(" · ")} · ctx {e.estimate?.context_length}</p>
          <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs">{e.reasons.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
          {e.limitations.length > 0 && <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-warn">{e.limitations.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>}
        </div>
      ))}
    </div>
  );
}

function InstallDialog({ repo, info, open, onOpenChange }: any) {
  const { t } = useI18n();
  const { me } = useMe();
  const { push } = useToast();
  const installers: any[] = info?.installers ?? [];
  const [engine, setEngine] = useState<string>(info?.analysis?.recommended_engine ?? "files");
  const current = installers.find((i) => i.engine === engine);
  const [include, setInclude] = useState("");
  const [exclude, setExclude] = useState("");
  const [force, setForce] = useState(false);
  const isManager = me?.role === "admin" || me?.role === "manager";
  const patterns = (v: string) => v.split(/[\n,]/).map((x) => x.trim()).filter(Boolean);
  const filters = () => ({ include_patterns: patterns(include).length ? patterns(include) : undefined, exclude_patterns: patterns(exclude).length ? patterns(exclude) : undefined });
  const [quant, setQuant] = useState("");
  const [ctx, setCtx] = useState("");
  const [plan, setPlan] = useState<any>(null);
  const [err, setErr] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [accept, setAccept] = useState(false);
  const [alias, setAlias] = useState("");
  const [vis, setVis] = useState(me?.role === "admin" || me?.role === "manager" ? "public" : "private");
  const [reason, setReason] = useState("");
  const ollama = info?.analysis?.engines?.find((e: any) => e.engine === "ollama")?.recommended_params ?? {};
  const variants = ollama.variants ?? {};
  const split: string[] = ollama.split ?? [];
  async function makePlan(forceNow: boolean = force) {
    setBusy(true); setErr(null);
    try {
      setPlan(await api("/api/v1/huggingface/plan", { method: "POST", json: { repo_id: repo, engine, gguf_quant: quant || undefined, context_length: ctx ? Number(ctx) : undefined, force: forceNow || undefined, ...filters() } }));
    } catch (e) { setErr(e); } finally { setBusy(false); }
  }
  async function install() {
    setBusy(true); setErr(null);
    try {
      const r = await api("/api/v1/huggingface/install", { method: "POST", json: { repo_id: repo, revision: plan.revision, engine, gguf_quant: quant || undefined, context_length: ctx ? Number(ctx) : undefined, force: force || undefined, ...filters(), alias: alias || undefined, visibility: vis, accept_license: accept, reason } });
      push("ok", r.status === "started" ? `Descarga iniciada (${r.alias})` : `${t("models.awaiting")}: ${r.alias}`);
      onOpenChange(false);
    } catch (e) { setErr(e); } finally { setBusy(false); }
  }
  const licWarn = plan?.warnings?.some((w: any) => w.code === "license");
  return (
    <Modal open={open} onOpenChange={onOpenChange} title={`${t("discover.install")} ${repo}`} description={t("discover.plan")} wide>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label={tr("Cómo instalarlo", "How to install")} hint={current?.summary}>
          <Select value={engine} onChange={(e) => { setEngine(e.target.value); setPlan(null); setForce(false); }}>
            {installers.map((i: any) => <option key={i.engine} value={i.engine} disabled={!i.usable}>
              {i.label}{i.recommended ? " · recomendado" : ""}{!i.usable ? " · no aplicable" : i.needs_force ? " · requiere forzar" : ""}</option>)}
          </Select>
        </Field>
        {engine === "ollama" && <Field label="GGUF"><Select value={quant} onChange={(e) => { setQuant(e.target.value); setPlan(null); }}><option value="">auto</option>{Object.entries(variants).map(([k, v]: any) => <option key={k} value={k.toUpperCase()}>{k.toUpperCase()} · {v} GB{split.includes(k) ? " · troceado" : ""}</option>)}</Select></Field>}
        {engine === "vllm" && <Field label="Context (max_model_len)"><Input type="number" value={ctx} placeholder="8192" onChange={(e) => { setCtx(e.target.value); setPlan(null); }} /></Field>}
      </div>
      {current && !current.usable && <div className="mt-2"><Notice tone="warn">{current.unusable_reason}</Notice></div>}
      <details className="mt-3 rounded-xl border border-border p-3 text-sm">
        <summary className="cursor-pointer font-medium">{tr("Opciones avanzadas: elegir qué archivos descargar", "Advanced options: choose which files to download")}</summary>
        <p className="mt-2 text-xs text-muted">Un patrón por línea o separados por comas (<code>*.safetensors</code>, <code>config.json</code>, <code>Q4_K_M/*</code>). Si no pones nada, se descarga lo que necesita el motor elegido. Con «Solo archivos» se baja todo salvo los duplicados .bin.</p>
        <div className="mt-2 grid gap-3 sm:grid-cols-2">
          <Field label={tr("Descargar solo", "Download only")}><Textarea rows={2} value={include} placeholder="*.safetensors&#10;config.json" onChange={(e) => { setInclude(e.target.value); setPlan(null); }} /></Field>
          <Field label={tr("Excluir", "Exclude")}><Textarea rows={2} value={exclude} placeholder="onnx/*&#10;*.md" onChange={(e) => { setExclude(e.target.value); setPlan(null); }} /></Field>
        </div>
      </details>
      <div className="mt-3"><Button onClick={() => makePlan()} loading={busy && !plan}>{t("discover.plan")}</Button></div>
      {err && <div className="mt-3"><ErrorBox error={err} /></div>}
      {plan && (
        <div className="mt-4 space-y-3">
          <ol className="grid gap-2 sm:grid-cols-5">
            {plan.steps.map((s: any) => (
              <li key={s.step} className={`rounded-lg border p-2 text-xs ${s.status === "blocked" ? "border-danger/40 bg-danger/5" : "border-border"}`}>
                <div className="font-medium capitalize">{s.step}</div><div className="truncate text-muted" title={String(s.detail)}>{String(s.detail ?? "—")}{s.step === "memory" && s.detail ? " GB" : ""}</div>
              </li>
            ))}
          </ol>
          <p className="text-xs text-muted">Revisión fijada: <code className="font-mono">{plan.revision}</code> · {bytes(plan.download_bytes)} · {plan.files.length} archivos · {plan.engine === "files" ? "solo archivos" : <>veredicto <StatusBadge state={plan.verdict} /></>}</p>
          {plan.warnings.map((w: any, i: number) => <Notice key={i} tone={w.level === "danger" ? "danger" : w.level === "warning" ? "warn" : "info"} icon={<ShieldAlert className="h-4 w-4" />}>{w.message}</Notice>)}
          {plan.blockers.map((b: string, i: number) => <Notice key={i} tone="danger" icon={<AlertTriangle className="h-4 w-4" />}>{b}</Notice>)}
          {plan.blockers.length > 0 && !plan.overridable && plan.engine !== "files" && <Notice tone="info">{tr("Si ningún motor lo admite, elige «Solo archivos» en «Cómo instalarlo»: se descarga y se registra sin cargarlo en ningún motor.", "If no engine supports it, choose “Files only” under “How to install”: it is downloaded and registered without loading it into any engine.")}</Notice>}
          {(plan.overridable || plan.forced) && isManager && <Switch checked={force} onChange={(v: boolean) => { setForce(v); makePlan(v); }} label={tr("Instalar de todos modos (ignora el veredicto de compatibilidad; el disco y los permisos se siguen respetando)", "Install anyway (ignores the compatibility verdict; disk space and permissions are still respected)")} />}
          {plan.requires_admin_approval && <>
            <Notice tone="info">{tr("Tu petición se enviará a los administradores y gestores. Te avisaremos (🔔 y correo) cuando se apruebe y cuando el modelo esté listo. Puedes seguirla en «Solicitudes».", "Your request will be sent to the administrators and managers. We will notify you (🔔 and e-mail) when it is approved and when the model is ready. You can follow it under “Requests”.")}</Notice>
            <Field label={tr("¿Para qué lo necesitas? (lo verá quien revise la solicitud)", "What do you need it for? (the reviewer will see this)")}><Textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder={tr("p. ej. Transcribir entrevistas del proyecto X en valenciano", "e.g. Transcribe interviews for project X in Valencian")} /></Field>
          </>}
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Alias" hint="Nombre expuesto en la API (/v1/models)"><Input value={alias} placeholder={repo.split("/")[1].toLowerCase()} onChange={(e) => setAlias(e.target.value)} /></Field>
            <Field label={t("common.visibility")}><Select value={vis} onChange={(e) => setVis(e.target.value)}><option value="private">{t("common.private")}</option>{(me?.role === "admin" || me?.role === "manager") && <option value="public">{t("common.public")}</option>}</Select></Field>
          </div>
          {licWarn && <Switch checked={accept} onChange={setAccept} label={`${t("discover.accept_license")} (${plan.license ?? "?"})`} />}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => onOpenChange(false)}>{t("common.cancel")}</Button>
            <Button variant="primary" disabled={plan.blockers.length > 0 || (licWarn && !accept)} loading={busy} onClick={install}><Download className="h-4 w-4" />{t("discover.install")}</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

export default function ModelDetail({ params }: { params: Promise<{ owner: string; name: string }> }) {
  const { owner, name } = use(params);
  const repo = `${owner}/${name}`;
  const { t, lang } = useI18n();
  const { data, error, isLoading } = useSWR(`/api/v1/huggingface/models/${repo}`, { revalidateOnFocus: false });
  const { data: card } = useSWR(`/api/v1/huggingface/models/${repo}/card`, { revalidateOnFocus: false });
  const { data: refs } = useSWR(`/api/v1/huggingface/models/${repo}/refs`, { revalidateOnFocus: false });
  const [open, setOpen] = useState(false);
  const { me } = useMe();
  return (
    <div>
      <Link href="/discover" className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-fg"><ArrowLeft className="h-3 w-3" />{t("discover.title")}</Link>
      <PageHeader title={repo} subtitle={data ? `${data.pipeline_tag ?? ""} · ${data.library ?? ""} · ${compact(data.downloads)} descargas · ${data.license ?? "licencia desconocida"}` : ""}
        icon={<Package className="h-5 w-5" />} actions={data && me?.role !== "viewer" && <Button variant="primary" onClick={() => setOpen(true)}><Download className="h-4 w-4" />{t("discover.install")}</Button>} />
      {error && <ErrorBox error={error} />}
      {isLoading && <Skeleton className="h-64" />}
      {data && (
        <>
          {data.warnings.length > 0 && <div className="mb-4 space-y-2">{data.warnings.map((w: any, i: number) => <Notice key={i} tone={w.level === "danger" ? "danger" : w.level === "warning" ? "warn" : "info"} icon={<ShieldAlert className="h-4 w-4" />}>{w.message}</Notice>)}</div>}
          {data.registered.length > 0 && <div className="mb-4"><Notice tone="ok">Registrado como: {data.registered.map((r: any) => <Link key={r.id} className="ml-1 underline" href={`/models/${r.id}`}>{r.alias} ({r.status})</Link>)}</Notice></div>}
          <Tabs tabs={[
            { id: "analysis", label: t("discover.analysis"), content: <Card><CardBody><Verdicts analysis={data.analysis} /></CardBody></Card> },
            { id: "card", label: t("discover.card"), content: <Card><CardBody className="prose-chat max-w-none text-sm">{card?.markdown ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{card.markdown.replace(/^---[\s\S]*?---/, "")}</ReactMarkdown> : <p className="text-muted">—</p>}</CardBody></Card> },
            { id: "files", label: `${t("discover.files")} (${data.files.length})`, content: (
              <Card><Table head={[t("common.name"), t("common.size"), "sha256 (LFS)"]}>
                {data.files.map((f: any) => <tr key={f.path}><Td className="font-mono text-xs">{f.path}</Td><Td className="tabular-nums">{bytes(f.size)}</Td><Td className="font-mono text-[10px] text-muted">{f.sha256?.slice(0, 16) ?? "—"}</Td></tr>)}
              </Table><div className="px-4 py-3 text-xs text-muted">Total {bytes(data.total_size)} · commit {data.sha}</div></Card>) },
            { id: "versions", label: "Versiones", content: (
              <Card><CardHeader title="Branches / tags / commits" icon={<FileText className="h-4 w-4" />} /><CardBody className="space-y-2 text-xs">
                {refs?.refs?.branches?.map((b: any) => <div key={b.name}><Badge>{b.name}</Badge> <code className="font-mono">{b.commit}</code></div>)}
                {refs?.refs?.tags?.map((b: any) => <div key={b.name}><Badge tone="accent">{b.name}</Badge> <code className="font-mono">{b.commit}</code></div>)}
                <ul className="mt-3 divide-y divide-border/60">{refs?.commits?.map((c: any) => <li key={c.commit} className="py-1.5"><code className="font-mono text-muted">{c.commit.slice(0, 10)}</code> {c.title} <span className="text-subtle">· {dt(c.date, lang)}</span></li>)}</ul>
              </CardBody></Card>) },
          ]} />
          {open && <InstallDialog repo={repo} info={data} open={open} onOpenChange={setOpen} />}
        </>
      )}
    </div>
  );
}
