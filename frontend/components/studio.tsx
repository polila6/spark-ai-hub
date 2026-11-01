"use client";
import { Download, ImagePlus, Info, Wand2, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { Badge, Button, Card, CardBody, CardHeader, Empty, ErrorBox, Field, Input, Modal, Notice, Progress, Select, Skeleton, StatusBadge, Textarea } from "@/components/ui";
import { api, upload } from "@/lib/api";
import { Guide } from "./guide";
import { bytes, dt, duration, num } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { useMe, useToast } from "@/lib/providers";

function ImageInput({ value, onChange }: { value?: string; onChange: (id?: string) => void }) {
  const { t } = useI18n();
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<any>(null);
  return (
    <div className="space-y-2">
      {value ? (
        <div className="relative w-fit">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={`/api/v1/media/artifacts/${value}/file`} alt="input" className="max-h-40 rounded-lg border border-border" />
          <button className="absolute right-1 top-1 rounded-full bg-black/60 p-1 text-white" aria-label={t("common.delete")} onClick={() => onChange(undefined)}><X className="h-3 w-3" /></button>
        </div>
      ) : (
        <label className="flex cursor-pointer items-center justify-center gap-2 rounded-xl border border-dashed border-border px-4 py-6 text-sm text-muted hover:border-accent hover:text-fg">
          <ImagePlus className="h-4 w-4" />{busy ? t("common.loading") : t("common.upload")}
          <input type="file" accept="image/png,image/jpeg,image/webp" className="sr-only" onChange={async (e) => {
            const f = e.target.files?.[0]; if (!f) return;
            setBusy(true); setErr(null);
            try { const fd = new FormData(); fd.append("file", f); const a = await upload("/api/v1/media/uploads?kind=image", fd); onChange(a.id); } catch (e) { setErr(e); } finally { setBusy(false); }
          }} />
        </label>
      )}
      {err && <ErrorBox error={err} />}
    </div>
  );
}

function ParamForm({ wf, values, setValues }: { wf: any; values: any; setValues: (v: any) => void }) {
  const { t } = useI18n();
  const entries = Object.entries(wf.params) as [string, any][];
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {entries.map(([k, s]) => {
        const v = values[k] ?? s.default ?? "";
        const set = (x: any) => setValues({ ...values, [k]: x });
        const label = k === "negative_prompt" ? t("media.negative") : k === "prompt" ? t("common.prompt") : k === "image" ? t("media.input_image") : k === "seed" ? t("common.seed") : k;
        if (s.type === "text") return <div key={k} className="sm:col-span-2"><Field label={label}><Textarea rows={k === "prompt" ? 3 : 2} value={v} onChange={(e) => set(e.target.value)} /></Field></div>;
        if (s.type === "image") return <div key={k} className="sm:col-span-2"><Field label={`${label}${s.required ? "" : ` (${t("common.optional")})`}`} hint={s.help}><ImageInput value={values[k]} onChange={set} /></Field></div>;
        if (s.type === "enum") return <Field key={k} label={label}><Select value={v} onChange={(e) => set(e.target.value)}>{s.values.map((o: string) => <option key={o}>{o}</option>)}</Select></Field>;
        return <Field key={k} label={label} hint={s.min !== undefined ? `${s.min}–${s.max}` : undefined}><Input type="number" step={s.type === "float" ? "0.05" : s.step ?? 1} min={s.min} max={s.max} value={v ?? ""} placeholder={k === "seed" ? "aleatoria" : ""} onChange={(e) => set(e.target.value === "" ? undefined : Number(e.target.value))} /></Field>;
      })}
    </div>
  );
}

export function Studio({ kind, title, subtitle, icon }: { kind: "image" | "video"; title: string; subtitle: string; icon: React.ReactNode }) {
  const { t, lang } = useI18n();
  const { me } = useMe();
  const { push } = useToast();
  const { data: wfs, mutate: mutWf } = useSWR(`/api/v1/media/workflows?kind=${kind}`, { refreshInterval: 15000 });
  const jobType = kind === "video" ? "video_generate" : "image_generate";
  const { data: jobs } = useSWR(`/api/v1/jobs?type=${jobType}&limit=8`, { refreshInterval: 2500 });
  const { data: arts, mutate: mutArts } = useSWR(`/api/v1/media/artifacts?kind=${kind}&limit=60`, { refreshInterval: 6000 });
  const [wfId, setWfId] = useState("");
  const [values, setValues] = useState<any>({});
  const [err, setErr] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const [view, setView] = useState<any>(null);
  const wf = useMemo(() => wfs?.workflows?.find((w: any) => w.id === wfId) ?? wfs?.workflows?.find((w: any) => w.available) ?? wfs?.workflows?.[0], [wfs, wfId]);
  useEffect(() => { if (wf && !wfId) setWfId(wf.id); }, [wf, wfId]);
  const running = jobs?.items?.filter((j: any) => ["queued", "preparing", "running"].includes(j.status)) ?? [];
  useEffect(() => { if (running.length === 0) mutArts(); }, [running.length, mutArts]);
  async function submit() {
    setBusy(true); setErr(null);
    try {
      const r = await api("/api/v1/media/generate", { method: "POST", json: { workflow_id: wf.id, inputs: values } });
      push("ok", `${t("media.generate")} · ~${r.estimate.memory_gb} GB (${t("common.estimated")})`);
    } catch (e) { setErr(e); } finally { setBusy(false); }
  }
  return (
    <div>
      <div className="mb-6 flex items-center gap-3"><div className="rounded-xl bg-gradient-to-br from-accent/20 to-info/10 p-2.5 text-accent">{icon}</div><div><h1 className="text-xl font-semibold sm:text-2xl">{title}</h1><p className="mt-1 text-sm text-muted">{subtitle}</p></div></div>
      <Guide id={kind} />
      <div className="grid gap-4 xl:grid-cols-[420px_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card>
            <CardHeader title={t("media.workflow")} />
            <CardBody className="space-y-4">
              {!wfs ? <Skeleton className="h-10" /> : (
                <Select value={wf?.id} onChange={(e) => { setWfId(e.target.value); setValues({}); }}>
                  {wfs.workflows.map((w: any) => <option key={w.id} value={w.id}>{w.name[lang] ?? w.name.en} {w.available ? "" : "· no instalado"}</option>)}
                </Select>
              )}
              {wf && (
                <>
                  <div className="flex flex-wrap gap-1 text-[11px]"><Badge>{wf.mode}</Badge><Badge>~{wf.memory_gb} GB</Badge>{wf.gpu_heavy && <Badge tone="warn">GPU-heavy · serializado</Badge>}{wf.models.map((m: any) => <Badge key={m.filename} tone={m.installed ? "ok" : "muted"} title={`${m.repo_id} · ${m.license}`}>{m.filename} ({m.size_gb} GB)</Badge>)}</div>
                  <p className="flex items-start gap-1 text-[11px] text-subtle"><Info className="mt-0.5 h-3 w-3 shrink-0" />{wf.source}</p>
                  {!wf.available ? (
                    <Notice tone="warn">
                      <p>{t("media.not_installed")} ({wf.install_size_gb} GB).{wf.missing_nodes?.length ? ` Nodos ausentes: ${wf.missing_nodes.join(", ")}` : ""}</p>
                      {(me?.role === "admin" || me?.role === "manager") && wf.install_size_gb > 0 && <Button size="sm" className="mt-2" onClick={async () => { try { const r = await api(`/api/v1/media/workflows/${wf.id}/install`, { method: "POST" }); push("ok", `${r.jobs.length} descargas`); mutWf(); } catch (e: any) { push("error", e.message); } }}>{t("media.install_wf")}</Button>}
                    </Notice>
                  ) : (
                    <>
                      <ParamForm wf={wf} values={values} setValues={setValues} />
                      {kind === "video" && <Notice tone="muted">Duración ≈ frames / fps = {num(((values.frames ?? wf.params.frames?.default) || 0) / ((values.fps ?? wf.params.fps?.default) || 1), 1)} s. La generación de vídeo tarda minutos.</Notice>}
                      {err && <ErrorBox error={err} />}
                      <Button variant="primary" className="w-full" loading={busy} onClick={submit} disabled={wf.params.prompt?.required && !values.prompt}><Wand2 className="h-4 w-4" />{t("media.generate")}</Button>
                    </>
                  )}
                </>
              )}
            </CardBody>
          </Card>
          {running.length > 0 && (
            <Card>
              <CardHeader title={t("dash.running")} />
              <CardBody className="space-y-3">
                {running.map((j: any) => (
                  <div key={j.id}>
                    <div className="mb-1 flex items-center justify-between gap-2 text-xs"><span className="truncate">{j.title}</span><StatusBadge state={j.status} /></div>
                    <Progress value={j.progress * 100} />
                    <div className="mt-1 flex justify-between text-[11px] text-muted"><span className="truncate">{j.message}</span><button className="text-danger hover:underline" onClick={() => api(`/api/v1/jobs/${j.id}/cancel`, { method: "POST" })}>{t("common.cancel")}</button></div>
                  </div>
                ))}
              </CardBody>
            </Card>
          )}
          {jobs?.items?.filter((j: any) => j.status === "failed").slice(0, 2).map((j: any) => <ErrorBox key={j.id} error={{ message: `${j.title}: ${j.error}` }} />)}
        </div>
        <Card>
          <CardHeader title={t("media.history")} subtitle={`${arts?.length ?? 0}`} />
          <CardBody>
            {!arts ? <Skeleton className="h-60" /> : arts.length === 0 ? <Empty /> : (
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3 2xl:grid-cols-4">
                {arts.map((a: any) => (
                  <button key={a.id} className="group overflow-hidden rounded-xl border border-border bg-elevated text-left transition hover:border-accent" onClick={() => setView(a)}>
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={a.url} alt={a.meta?.params?.prompt ?? a.filename} loading="lazy" className="aspect-square w-full object-cover transition group-hover:scale-[1.02]" />
                    <div className="truncate px-2 py-1.5 text-[11px] text-muted">{a.meta?.params?.prompt ?? a.filename}</div>
                  </button>
                ))}
              </div>
            )}
          </CardBody>
        </Card>
      </div>
      {view && (
        <Modal open onOpenChange={(o) => !o && setView(null)} title={view.filename} wide>
          <div className="grid gap-4 md:grid-cols-[1fr_280px]">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={view.url} alt={view.meta?.params?.prompt ?? ""} className="w-full rounded-xl border border-border" />
            <div className="space-y-2 text-xs">
              <a href={`${view.url}?download=true`}><Button size="sm" variant="primary" className="w-full"><Download className="h-3.5 w-3.5" />{t("common.download")}</Button></a>
              {[["workflow", view.meta?.workflow], ["seed", view.meta?.params?.seed], ["steps", view.meta?.params?.steps], ["cfg", view.meta?.params?.cfg], ["sampler", view.meta?.params?.sampler],
                ["size", `${view.meta?.width ?? "?"}×${view.meta?.height ?? "?"}`], ["frames/fps", view.meta?.frames ? `${view.meta.frames} @ ${view.meta.fps}` : null], ["duración", view.meta?.duration_s ? `${view.meta.duration_s} s` : null],
                ["tiempo", duration(view.meta?.generation_seconds)], ["archivo", bytes(view.size_bytes)], ["sha256", view.sha256?.slice(0, 16)], [t("common.created"), dt(view.created_at, lang)]]
                .filter(([, v]) => v).map(([k, v]: any) => <div key={k} className="flex justify-between gap-2 border-b border-border/50 pb-1"><span className="text-muted">{k}</span><span className="text-right">{String(v)}</span></div>)}
              {view.meta?.params?.prompt && <p className="rounded-lg bg-elevated p-2">{view.meta.params.prompt}</p>}
              {view.job_id && <a className="text-accent hover:underline" href={`/jobs?open=${view.job_id}`}>workflow.json → {t("nav.jobs")}</a>}
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}
