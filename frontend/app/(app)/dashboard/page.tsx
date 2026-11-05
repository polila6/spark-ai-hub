"use client";
import { AlertTriangle, Boxes, Cpu, Gauge, HardDrive, ListChecks, MemoryStick, Server, Thermometer, Zap } from "lucide-react";
import Link from "next/link";
import useSWR from "swr";
import { Spark } from "@/components/charts";
import { Guide } from "@/components/guide";
import { MemoryPanel } from "@/components/memory";
import { Badge, Card, CardBody, CardHeader, Empty, Notice, PageHeader, Progress, Skeleton, StatusBadge, Tip } from "@/components/ui";
import { ago, bytes, num, pct, rate } from "@/lib/format";
import { useI18n } from "@/lib/i18n";

const ENGINE_NAMES: Record<string, string> = { ollama: "Ollama", vllm: "vLLM", comfyui: "ComfyUI", mlworker: "ML worker" };

function Metric({ label, value, sub, icon, spark, unavailable, tip }: any) {
  return (
    <Card className="flex flex-col p-4">
      <div className="flex items-center justify-between">
        <span className="flex items-center gap-1 text-xs font-medium text-muted">{label}{tip && <Tip text={tip} />}</span>
        <span className="rounded-lg bg-elevated p-1.5 text-muted">{icon}</span>
      </div>
      <div className={`mt-2 text-2xl font-semibold tabular-nums tracking-tight ${unavailable ? "text-subtle" : "text-fg"}`}>{value}</div>
      <div className="mt-0.5 text-xs text-muted">{sub}</div>
      <div className="mt-auto pt-3">{spark}</div>
    </Card>
  );
}

export default function Dashboard() {
  const { t, lang } = useI18n();
  const { data: m, error: mErr } = useSWR("/api/v1/metrics/current", { refreshInterval: 5000 });
  const { data: recent } = useSWR("/api/v1/metrics/recent?limit=180", { refreshInterval: 15000 });
  const { data: st } = useSWR("/api/v1/system/status", { refreshInterval: 10000 });
  const { data: alerts } = useSWR("/api/v1/metrics/alerts", { refreshInterval: 30000 });
  const series = (recent || []).map((r: any) => ({ ...r, mem_gb: r.mem_available_gb }));
  const mem = m?.memory;
  const g = m?.gpu;
  const disk = m?.disks?.[0];
  const b = st?.budget;
  const unav = t("common.unavailable");
  return (
    <div>
      <PageHeader title={t("dash.title")} subtitle={t("dash.subtitle")} icon={<Gauge className="h-5 w-5" />} />
      <Guide id="welcome" />
      <div className="mb-5"><MemoryPanel /></div>
      {mErr && <div className="mb-4"><Notice tone="warn">{mErr.message}</Notice></div>}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {!m ? Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-36" />) : (
          <>
            <Metric label={t("dash.cpu")} icon={<Cpu className="h-4 w-4" />} value={pct(m.cpu_percent)}
              sub={`load ${m.load_avg?.map((x: number) => x.toFixed(2)).join(" · ") ?? "—"} · ${num(m.thermal?.cpu_max_c, 0, " °C")}`}
              spark={<Spark data={series} dataKey="cpu_percent" domain={[0, 100]} />} />
            <Metric label={t("dash.memory")} icon={<MemoryStick className="h-4 w-4" />} tip={t("dash.uma_note")}
              value={pct(mem?.used_percent)} sub={`${bytes(mem?.used_bytes)} / ${bytes(mem?.total_bytes)} · ${t("dash.available_live")} ${bytes(mem?.available_bytes)}`}
              spark={<Spark data={series} dataKey="mem_used_percent" color="rgb(var(--info))" domain={[0, 100]} />} />
            <Metric label={t("dash.gpu")} icon={<Zap className="h-4 w-4" />} unavailable={!g?.available}
              value={g?.available ? pct(g.utilization_percent) : unav}
              sub={g?.available ? `${num(g.temperature_c, 0, " °C")} · ${num(g.power_w, 1, " W")} · ${num(g.sm_clock_mhz, 0, " MHz")}` : g?.reason}
              spark={<Spark data={series} dataKey="gpu_util" color="rgb(var(--warn))" domain={[0, 100]} />} />
            <Metric label={t("dash.disk")} icon={<HardDrive className="h-4 w-4" />} value={pct(disk?.used_percent)}
              sub={`${bytes(disk?.free_bytes)} libres de ${bytes(disk?.total_bytes)} · R ${rate(m.disk_io?.read_bps)} · W ${rate(m.disk_io?.write_bps)}`}
              spark={<Spark data={series} dataKey="disk_write_bps" color="rgb(var(--ok))" />} />
          </>
        )}
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title={t("dash.budget")} subtitle={b ? `${t("settings.scheduler")}: ${b.mode} · max GPU-heavy ${b.max_concurrent_gpu_jobs}` : ""} icon={<Server className="h-4 w-4" />} />
          <CardBody>
            {!b ? <Skeleton className="h-16" /> : (
              <div className="space-y-4">
                <div>
                  <div className="mb-1.5 flex justify-between text-xs text-muted"><span>{t("dash.reserved")}: <b className="text-fg">{num(b.reserved_gb)} GB</b> / {num(b.schedulable_gb)} GB</span><span>{t("dash.available_live")}: {num(b.mem_available_gb)} GB</span></div>
                  <Progress value={(b.reserved_gb / b.schedulable_gb) * 100} tone={b.reserved_gb / b.schedulable_gb > 0.85 ? "warn" : "accent"} />
                  <p className="mt-1.5 text-[11px] text-subtle">MemTotal {num(b.total_gb)} GB − reserva del sistema {num(b.system_reserve_gb)} GB. {t("dash.uma_note")}</p>
                </div>
                <div>
                  <p className="mb-2 text-xs font-medium text-muted">{t("dash.active_models")}</p>
                  {st.runtime_instances.length === 0 ? <p className="text-sm text-subtle">—</p> : (
                    <ul className="divide-y divide-border/60 rounded-xl border border-border">
                      {st.runtime_instances.map((i: any) => (
                        <li key={i.id} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
                          <StatusBadge state={i.state} />
                          <span className="font-medium">{i.model ?? i.engine}</span>
                          <Badge>{i.engine}</Badge>
                          {i.pinned && <Badge tone="accent">pinned</Badge>}
                          <span className="ml-auto text-xs text-muted">
                            {i.measured_memory_gb ? `${num(i.measured_memory_gb)} GB ${t("common.measured")}` : `${num(i.reserved_memory_gb)} GB ${t("common.estimated")}`}
                            {" · "}{i.load_seconds ? `load ${num(i.load_seconds)} s` : ""} · {ago(i.last_used_at, lang)}
                            {i.active_requests > 0 && ` · ${i.active_requests} req`}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title={t("dash.jobs")} icon={<ListChecks className="h-4 w-4" />} actions={<Link className="text-xs text-accent hover:underline" href="/jobs">→</Link>} />
          <CardBody className="grid grid-cols-3 gap-3 text-center">
            {[["dash.queued", st?.jobs?.queued], ["dash.running", st?.jobs?.running], ["dash.failed24", st?.jobs?.failed_24h]].map(([k, v]: any) => (
              <div key={k} className="rounded-xl bg-elevated/70 p-3"><div className="text-2xl font-semibold tabular-nums">{v ?? "—"}</div><div className="text-[11px] text-muted">{t(k)}</div></div>
            ))}
          </CardBody>
        </Card>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-3">
        <Card className="xl:col-span-2">
          <CardHeader title={t("dash.engines")} icon={<Boxes className="h-4 w-4" />} />
          <CardBody className="grid gap-3 sm:grid-cols-2">
            {!st ? <Skeleton className="h-24" /> : Object.entries(st.engines).map(([name, e]: any) => (
              <div key={name} className="rounded-xl border border-border p-3">
                <div className="flex items-center justify-between"><span className="font-medium">{ENGINE_NAMES[name] ?? name}</span><StatusBadge state={e.status} /></div>
                <p className="mt-1 truncate text-xs text-muted" title={e.image || e.detail}>{e.version ? `v${e.version}` : ""} {e.image ?? ""} {e.detail && !e.version ? e.detail : ""}</p>
                <div className="mt-2 flex flex-wrap gap-1">{e.capabilities.map((c: string) => <Badge key={c}>{c}</Badge>)}</div>
              </div>
            ))}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title={t("dash.alerts")} icon={<AlertTriangle className="h-4 w-4" />} />
          <CardBody>
            {!alerts ? <Skeleton className="h-20" /> : alerts.length === 0 ? <Empty title={t("dash.no_alerts")} /> : (
              <ul className="space-y-2">
                {alerts.map((a: any) => (
                  <li key={a.id} className="rounded-xl border border-border p-3 text-sm">
                    <div className="flex items-center gap-2"><Badge tone={a.severity === "critical" ? "danger" : "warn"}>{a.severity}</Badge><span className="text-xs text-muted">{ago(a.last_seen, lang)}</span></div>
                    <p className="mt-1 text-fg">{a.message}</p>
                  </li>
                ))}
              </ul>
            )}
          </CardBody>
        </Card>
      </div>
      {m && (
        <p className="mt-4 flex items-center gap-1.5 text-[11px] text-subtle"><Thermometer className="h-3 w-3" />
          SoC: {m.thermal?.zones?.map((z: any) => `${z.celsius.toFixed(0)}°`).join(" ")} · NVMe: {m.thermal?.nvme?.map((z: any) => `${z.label} ${z.celsius.toFixed(0)}°`).join(" · ")} · net ↓{rate(m.net_io?.rx_bps)} ↑{rate(m.net_io?.tx_bps)} · PSI mem {m.pressure?.memory?.some?.avg10 ?? "—"}
        </p>
      )}
    </div>
  );
}
