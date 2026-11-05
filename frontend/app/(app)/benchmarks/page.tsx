"use client";
import { BarChart3, Download } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { Bars } from "@/components/charts";
import { Guide } from "@/components/guide";
import { Badge, Button, Card, CardBody, CardHeader, Empty, ErrorBox, Field, Input, Modal, Notice, PageHeader, Select, Skeleton, StatusBadge, Table, Td } from "@/components/ui";
import { api } from "@/lib/api";
import { dt, num } from "@/lib/format";
import { useI18n, tr } from "@/lib/i18n";
import { useToast } from "@/lib/providers";

function RunDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const { t } = useI18n();
  const { data } = useSWR(`/api/v1/benchmarks/${id}`);
  return (
    <Modal open onOpenChange={(o) => !o && onClose()} title={data ? `${data.suite} · ${data.model?.alias}` : "…"} wide>
      {!data ? <Skeleton className="h-40" /> : (
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2 text-xs">
            <a href={`/api/v1/benchmarks/${id}/export?fmt=csv`}><Button size="sm" variant="outline"><Download className="h-3.5 w-3.5" />CSV</Button></a>
            <a href={`/api/v1/benchmarks/${id}/export?fmt=json`}><Button size="sm" variant="outline"><Download className="h-3.5 w-3.5" />JSON</Button></a>
          </div>
          <div className="grid gap-3 text-xs sm:grid-cols-2">
            <Card className="p-3"><p className="mb-1 font-medium">{t("common.model")}</p><pre className="whitespace-pre-wrap font-mono text-[10px] text-muted">{JSON.stringify(data.model, null, 2)}</pre></Card>
            <Card className="p-3"><p className="mb-1 font-medium">{t("bench.env")}</p><pre className="whitespace-pre-wrap font-mono text-[10px] text-muted">{JSON.stringify({ ...data.environment, config: data.config }, null, 2)}</pre></Card>
          </div>
          <Card className="p-3"><p className="mb-1 text-xs font-medium">Resumen</p><pre className="whitespace-pre-wrap font-mono text-[11px]">{JSON.stringify(data.summary, null, 2)}</pre></Card>
          <Table head={["#", "case", "conc", "TTFT ms", "lat ms", "tokens", "tok/s", "score", "error"]}>
            {data.samples.map((s: any) => (
              <tr key={s.idx}><Td>{s.idx}</Td><Td>{s.case_id}</Td><Td>{s.concurrency}</Td><Td>{num(s.ttft_ms, 0)}</Td><Td>{num(s.latency_ms, 0)}</Td><Td>{s.completion_tokens ?? "—"}</Td><Td>{num(s.tokens_per_s)}</Td><Td>{s.score ?? "—"}</Td><Td className="max-w-[200px] truncate text-danger">{s.error}</Td></tr>
            ))}
          </Table>
        </div>
      )}
    </Modal>
  );
}

export default function BenchmarksPage() {
  const { t, lang } = useI18n();
  const { push } = useToast();
  const { data: suites } = useSWR("/api/v1/benchmarks/suites");
  const { data: models } = useSWR("/api/v1/models");
  const { data: runs, error, mutate } = useSWR("/api/v1/benchmarks", { refreshInterval: 5000 });
  const llms = (models ?? []).filter((m: any) => ["ollama", "vllm"].includes(m.backend) && m.capabilities?.includes("chat") && ["installed", "ready"].includes(m.status));
  const [f, setF] = useState({ model_id: "", suite: "perf-short-v1", temperature: 0, seed: 1234 });
  const [open, setOpen] = useState<string | null>(null);
  const [suiteFilter, setSuiteFilter] = useState("perf-short-v1");
  async function create() {
    try { await api("/api/v1/benchmarks", { method: "POST", json: { ...f, model_id: f.model_id || llms[0]?.id } }); push("ok", t("bench.new")); mutate(); } catch (e: any) { push("error", e.message); }
  }
  const done = (runs ?? []).filter((r: any) => r.status === "completed" && r.suite === suiteFilter);
  const latest: Record<string, any> = {};
  for (const r of done) if (!latest[r.model?.alias]) latest[r.model?.alias] = r;
  const isPerf = suites?.find((s: any) => s.id === suiteFilter)?.kind === "llm_perf";
  const chart = Object.values(latest).map((r: any) => isPerf
    ? { name: r.model.alias, tps: r.summary.tokens_per_s, ttft: r.summary.ttft_ms_p50, thr: r.summary.by_concurrency?.["4"]?.throughput_tps_mean }
    : { name: r.model.alias, score: Math.round((r.summary.score ?? 0) * 100) });
  return (
    <div>
      <PageHeader title={t("bench.title")} subtitle={t("bench.subtitle")} icon={<BarChart3 className="h-5 w-5" />} />
      <Guide id="benchmarks" />
      <div className="grid gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
        <Card>
          <CardHeader title={t("bench.new")} />
          <CardBody className="space-y-3">
            <Field label={t("common.model")}><Select value={f.model_id} onChange={(e) => setF({ ...f, model_id: e.target.value })}>{llms.map((m: any) => <option key={m.id} value={m.id}>{m.alias} · {m.backend}</option>)}</Select></Field>
            <Field label={t("bench.suite")}><Select value={f.suite} onChange={(e) => setF({ ...f, suite: e.target.value })}>{suites?.map((s: any) => <option key={s.id} value={s.id}>{s.id}</option>)}</Select></Field>
            <p className="text-xs text-muted">{suites?.find((s: any) => s.id === f.suite)?.description}</p>
            <div className="grid grid-cols-2 gap-3">
              <Field label={t("play.temperature")}><Input type="number" step={0.1} value={f.temperature} onChange={(e) => setF({ ...f, temperature: Number(e.target.value) })} /></Field>
              <Field label={t("common.seed")}><Input type="number" value={f.seed} onChange={(e) => setF({ ...f, seed: Number(e.target.value) })} /></Field>
            </div>
            <Notice tone="muted">{tr("Puntuaciones deterministas (coincidencia exacta / JSON válido). No hay juez LLM en estas suites.", "Deterministic scores (exact match / valid JSON). There is no LLM judge in these suites.")}</Notice>
            <Button variant="primary" className="w-full" disabled={!llms.length} onClick={create}>{t("common.run")}</Button>
          </CardBody>
        </Card>
        <div className="space-y-4">
          <Card>
            <CardHeader title={tr("Comparativa (última ejecución por modelo)", "Comparison (latest run per model)")} actions={<Select className="w-48" value={suiteFilter} onChange={(e) => setSuiteFilter(e.target.value)}>{suites?.map((s: any) => <option key={s.id}>{s.id}</option>)}</Select>} />
            <CardBody>{chart.length === 0 ? <Empty /> : isPerf
              ? <Bars data={chart} xKey="name" bars={[{ key: "tps", name: "tok/s (c=1)" }, { key: "thr", name: "throughput tok/s (c=4)" }, { key: "ttft", name: "TTFT p50 ms" }]} />
              : <Bars data={chart} xKey="name" unit="%" bars={[{ key: "score", name: t("bench.score") }]} />}</CardBody>
          </Card>
          <Card>
            {error && <ErrorBox error={error} />}
            <Table head={[t("common.created"), t("common.model"), t("bench.suite"), t("common.status"), t("bench.ttft"), t("bench.tps"), t("bench.score"), "err %", ""]}>
              {runs?.map((r: any) => (
                <tr key={r.id} className="hover:bg-elevated/40">
                  <Td className="whitespace-nowrap text-xs text-muted">{dt(r.created_at, lang)}</Td>
                  <Td><span className="font-medium">{r.model?.alias}</span><div className="text-[10px] text-muted">{r.model?.backend} {r.model?.quantization}</div></Td>
                  <Td><Badge>{r.suite}</Badge></Td>
                  <Td><StatusBadge state={r.status} /></Td>
                  <Td className="tabular-nums">{num(r.summary?.ttft_ms_p50, 0)}</Td>
                  <Td className="tabular-nums">{num(r.summary?.tokens_per_s)}</Td>
                  <Td className="tabular-nums">{r.summary?.score != null ? `${Math.round(r.summary.score * 100)} %` : "—"}</Td>
                  <Td className="tabular-nums">{r.summary?.error_rate != null ? `${Math.round(r.summary.error_rate * 100)}` : "—"}</Td>
                  <Td><Button size="sm" variant="ghost" onClick={() => setOpen(r.id)}>{t("common.details")}</Button></Td>
                </tr>
              ))}
            </Table>
          </Card>
        </div>
      </div>
      {open && <RunDetail id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
