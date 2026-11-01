"use client";
import { Download, Heart, Lock, Search, Sparkles } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { Guide } from "@/components/guide";
import { Badge, Button, Card, CardBody, CardHeader, Empty, ErrorBox, Field, Input, PageHeader, Select, Skeleton, StatusBadge, Switch, Tabs } from "@/components/ui";
import { api } from "@/lib/api";
import { ago, compact, num } from "@/lib/format";
import { useI18n } from "@/lib/i18n";

const TASKS = ["", "text-generation", "image-text-to-text", "feature-extraction", "text-to-image", "text-to-video", "automatic-speech-recognition", "text-to-speech", "image-classification"];
const LIBS = ["", "transformers", "gguf", "diffusers", "sentence-transformers", "timm", "mlx"];
const QUANTS = ["", "gguf", "fp8", "nvfp4", "awq", "gptq", "mxfp4", "bnb"];

function SearchTab() {
  const { t, lang } = useI18n();
  const [q, setQ] = useState("");
  const [params, setParams] = useState({ q: "", task: "", library: "", quantization: "", sort: "downloads" });
  const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as any).toString();
  const { data, error, isLoading, mutate } = useSWR(`/api/v1/huggingface/search?${qs}&limit=40`, { revalidateOnFocus: false });
  return (
    <div>
      <form onSubmit={(e) => { e.preventDefault(); setParams((p) => ({ ...p, q })); }} className="grid gap-3 md:grid-cols-[1fr_auto]">
        <div className="relative">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-subtle" />
          <Input aria-label={t("common.search")} className="h-11 pl-9" placeholder={t("discover.placeholder")} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <Button variant="primary" size="lg" type="submit">{t("common.search")}</Button>
      </form>
      <div className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4">
        <Field label={t("discover.task")}><Select value={params.task} onChange={(e) => setParams({ ...params, task: e.target.value })}>{TASKS.map((x) => <option key={x} value={x}>{x || t("common.all")}</option>)}</Select></Field>
        <Field label={t("discover.library")}><Select value={params.library} onChange={(e) => setParams({ ...params, library: e.target.value })}>{LIBS.map((x) => <option key={x} value={x}>{x || t("common.all")}</option>)}</Select></Field>
        <Field label={t("discover.quant")}><Select value={params.quantization} onChange={(e) => setParams({ ...params, quantization: e.target.value })}>{QUANTS.map((x) => <option key={x} value={x}>{x || t("common.all")}</option>)}</Select></Field>
        <Field label={t("discover.sort")}><Select value={params.sort} onChange={(e) => setParams({ ...params, sort: e.target.value })}>{["downloads", "likes", "lastModified", "trendingScore"].map((x) => <option key={x}>{x}</option>)}</Select></Field>
      </div>
      <div className="mt-5">
        {error && <ErrorBox error={error} onRetry={() => mutate()} />}
        {isLoading && <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{Array.from({ length: 9 }).map((_, i) => <Skeleton key={i} className="h-32" />)}</div>}
        {data && data.length === 0 && <Empty />}
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {data?.map((m: any) => (
            <Link key={m.id} href={`/discover/${m.id}`} className="group">
              <Card className="h-full p-4 transition group-hover:border-accent/50 group-hover:shadow-lg">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0"><p className="truncate text-xs text-muted">{m.author}</p><p className="truncate font-medium text-fg">{m.id.split("/")[1]}</p></div>
                  {m.gated && <Badge tone="warn"><Lock className="h-3 w-3" />gated</Badge>}
                </div>
                <div className="mt-2 flex flex-wrap gap-1">
                  {m.pipeline_tag && <Badge tone="info">{m.pipeline_tag}</Badge>}
                  {m.library && <Badge>{m.library}</Badge>}
                  {m.quantization && <Badge tone="accent">{m.quantization}</Badge>}
                  {m.license && <Badge>{m.license}</Badge>}
                </div>
                <div className="mt-3 flex items-center gap-4 text-xs text-muted">
                  <span className="flex items-center gap-1"><Download className="h-3 w-3" />{compact(m.downloads)}</span>
                  <span className="flex items-center gap-1"><Heart className="h-3 w-3" />{compact(m.likes)}</span>
                  {m.parameters && <span>{num(m.parameters / 1e9, 1)}B</span>}
                  <span className="ml-auto">{ago(m.last_modified, lang)}</span>
                </div>
              </Card>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}

function RecommendTab() {
  const { t } = useI18n();
  const [f, setF] = useState<any>({ task: "text-generation", max_memory_gb: "", max_disk_gb: "", min_context: "", speed: "balanced", allow_experimental: false, query: "" });
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true); setErr(null);
    try {
      const body: any = { ...f };
      for (const k of ["max_memory_gb", "max_disk_gb", "min_context"]) body[k] = body[k] ? Number(body[k]) : null;
      if (!body.query) delete body.query;
      setRes(await api("/api/v1/recommendations", { method: "POST", json: body }));
    } catch (e) { setErr(e); } finally { setBusy(false); }
  }
  return (
    <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
      <Card>
        <CardHeader title={t("rec.title")} subtitle={t("rec.subtitle")} icon={<Sparkles className="h-4 w-4" />} />
        <CardBody className="space-y-3">
          <Field label={t("discover.task")}><Select value={f.task} onChange={(e) => setF({ ...f, task: e.target.value })}>{TASKS.filter(Boolean).map((x) => <option key={x}>{x}</option>)}</Select></Field>
          <Field label={t("common.search")}><Input value={f.query} placeholder="qwen, llama, gemma…" onChange={(e) => setF({ ...f, query: e.target.value })} /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label={t("rec.max_mem")}><Input type="number" value={f.max_memory_gb} onChange={(e) => setF({ ...f, max_memory_gb: e.target.value })} /></Field>
            <Field label={t("rec.max_disk")}><Input type="number" value={f.max_disk_gb} onChange={(e) => setF({ ...f, max_disk_gb: e.target.value })} /></Field>
          </div>
          <Field label={t("rec.min_ctx")}><Input type="number" value={f.min_context} onChange={(e) => setF({ ...f, min_context: e.target.value })} /></Field>
          <Field label={t("rec.speed")}><Select value={f.speed} onChange={(e) => setF({ ...f, speed: e.target.value })}>{["fast", "balanced", "quality"].map((x) => <option key={x} value={x}>{t(`rec.speed.${x}`)}</option>)}</Select></Field>
          <Switch checked={f.allow_experimental} onChange={(v) => setF({ ...f, allow_experimental: v })} label={t("rec.experimental")} />
          <Button variant="primary" className="w-full" loading={busy} onClick={run}>{t("common.run")}</Button>
        </CardBody>
      </Card>
      <div className="space-y-3">
        {err && <ErrorBox error={err} />}
        {busy && <Skeleton className="h-40" />}
        {res && <p className="text-xs text-muted">{res.method} · budget {num(res.budget_gb)} GB</p>}
        {res?.results?.length === 0 && <Empty />}
        {res?.results?.map((r: any) => (
          <Card key={r.repo_id} className="p-4">
            <div className="flex flex-wrap items-center gap-2">
              <Link href={`/discover/${r.repo_id}`} className="font-medium text-fg hover:text-accent">{r.repo_id}</Link>
              <StatusBadge state={r.verdict} />
              <Badge>{r.engine}</Badge>
              {r.quantization && <Badge tone="accent">{r.quantization}</Badge>}
              {r.source === "nvidia_spark_matrix" && <Badge tone="ok">NVIDIA Spark</Badge>}
              <span className="ml-auto text-xs text-muted">score {r.score} · {t("discover.confidence")} {Math.round(r.confidence * 100)} %</span>
            </div>
            <p className="mt-2 text-xs text-muted">{t("discover.estimate")}: <b className="text-fg">{num(r.estimated_memory_gb)} GB</b> ({t("common.estimated")}) · {t("discover.disk")}: {num(r.disk_gb)} GB · {r.parameters_b ? `${r.parameters_b}B` : ""} · {r.license}</p>
            <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-fg/90">{r.reasons.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>
            {r.limitations.length > 0 && <ul className="mt-1 list-disc space-y-0.5 pl-5 text-xs text-warn">{r.limitations.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul>}
          </Card>
        ))}
      </div>
    </div>
  );
}

export default function DiscoverPage() {
  const { t } = useI18n();
  return (
    <div>
      <PageHeader title={t("discover.title")} subtitle={t("discover.subtitle")} icon={<Search className="h-5 w-5" />} />
      <Guide id="discover" />
      <Tabs tabs={[{ id: "search", label: t("common.search"), content: <SearchTab /> }, { id: "rec", label: t("discover.recommend"), content: <RecommendTab /> }]} />
    </div>
  );
}
