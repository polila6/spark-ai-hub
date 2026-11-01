"use client";
import { Activity } from "lucide-react";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { TimeChart } from "@/components/charts";
import { Guide } from "@/components/guide";
import { MemoryPanel } from "@/components/memory";
import { Badge, Button, Card, CardBody, CardHeader, Empty, Modal, PageHeader, Select, Skeleton, StatusBadge, Table, Td } from "@/components/ui";
import { api } from "@/lib/api";
import { ago, bytes, dt, num } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { useMe } from "@/lib/providers";

export default function MonitoringPage() {
  const { t, lang } = useI18n();
  const { me } = useMe();
  const [hours, setHours] = useState(6);
  const { data: hist } = useSWR(`/api/v1/metrics/history?hours=${hours}`, { refreshInterval: 60000 });
  const { data: cont } = useSWR(me?.role === "admin" ? "/api/v1/system/containers" : null, { refreshInterval: 20000 });
  const { data: alerts, mutate: mutAlerts } = useSWR("/api/v1/metrics/alerts?include_resolved=true", { refreshInterval: 30000 });
  const [live, setLive] = useState<any>(null);
  const [logs, setLogs] = useState<{ name: string; text: string } | null>(null);
  useEffect(() => {
    const es = new EventSource("/api/v1/metrics/stream");
    es.onmessage = (e) => { try { setLive(JSON.parse(e.data)); } catch { /* ignore */ } };
    return () => es.close();
  }, []);
  const pts = (hist?.points ?? []).map((p: any) => ({ ...p, label: new Date(p.ts).toLocaleTimeString(lang, { hour: "2-digit", minute: "2-digit" }), disk_r: p.disk_read_bps / 1e6, disk_w: p.disk_write_bps / 1e6, rx: p.net_rx_bps / 1e6, tx: p.net_tx_bps / 1e6 }));
  return (
    <div>
      <PageHeader title={t("mon.title")} subtitle={t("mon.subtitle")} icon={<Activity className="h-5 w-5" />}
        actions={<Select className="w-36" value={hours} onChange={(e) => setHours(Number(e.target.value))}>{[1, 6, 24, 72, 168].map((h) => <option key={h} value={h}>{h} h</option>)}</Select>} />
      <Guide id="monitoring" />
      <div className="mb-5"><MemoryPanel /></div>
      {live && (
        <Card className="mb-4"><CardBody className="grid gap-3 text-xs sm:grid-cols-3 xl:grid-cols-6">
          {[["CPU", `${num(live.cpu_percent)} %`], ["Mem", `${num(live.memory?.used_percent)} % · ${bytes(live.memory?.available_bytes)} libres`], ["GPU", live.gpu?.available ? `${num(live.gpu.utilization_percent, 0)} % · ${num(live.gpu.temperature_c, 0)} °C · ${num(live.gpu.power_w)} W` : t("common.unavailable")],
            ["GPU mem", live.gpu?.memory_used_mib != null ? `${live.gpu.memory_used_mib} MiB` : "no soportado (UMA)"], ["PSI mem avg10", live.pressure?.memory?.some?.avg10 ?? "—"], ["Swap", bytes(live.memory?.swap_used_bytes)]]
            .map(([k, v]) => <div key={k as string} className="rounded-lg bg-elevated p-2"><div className="text-muted">{k}</div><div className="font-medium tabular-nums">{v}</div></div>)}
        </CardBody></Card>
      )}
      <div className="grid gap-4 xl:grid-cols-2">
        {!hist ? <Skeleton className="h-60 xl:col-span-2" /> : pts.length === 0 ? <Card className="xl:col-span-2"><Empty hint="Se guarda una muestra por minuto." /></Card> : (
          <>
            <Card><CardHeader title="CPU / GPU (%)" subtitle={`${t("common.measured")} · resolución ${hist.resolution}`} /><CardBody><TimeChart data={pts} unit="%" lines={[{ key: "cpu_percent", name: "CPU" }, { key: "gpu_util", name: "GPU" }, { key: "mem_used_percent", name: "Mem" }]} /></CardBody></Card>
            <Card><CardHeader title="Temperatura (°C) y potencia GPU (W)" /><CardBody><TimeChart data={pts} lines={[{ key: "gpu_temp", name: "GPU °C" }, { key: "cpu_temp_max", name: "SoC máx °C" }, { key: "gpu_power", name: "GPU W" }]} /></CardBody></Card>
            <Card><CardHeader title="Disco (MB/s)" /><CardBody><TimeChart data={pts} lines={[{ key: "disk_r", name: "lectura" }, { key: "disk_w", name: "escritura" }]} /></CardBody></Card>
            <Card><CardHeader title="Red (MB/s)" /><CardBody><TimeChart data={pts} lines={[{ key: "rx", name: "rx" }, { key: "tx", name: "tx" }]} /></CardBody></Card>
          </>
        )}
      </div>
      {me?.role === "admin" && (
        <Card className="mt-4">
          <CardHeader title={t("mon.containers")} />
          {!cont ? <Skeleton className="h-32" /> : (
            <Table head={[t("common.name"), "imagen", t("common.status"), "health", "memoria", "CPU", "inicio", ""]}>
              {cont.containers.map((c: any) => (
                <tr key={c.name}><Td className="font-medium">{c.service ?? c.engine ?? c.name}</Td><Td className="max-w-[240px] truncate font-mono text-[10px] text-muted">{c.image}</Td>
                  <Td><StatusBadge state={c.state === "running" ? "ok" : c.state === "exited" ? "stopped" : c.state} label={c.state} /></Td>
                  <Td>{c.health ? <Badge tone={c.health === "healthy" ? "ok" : c.health === "starting" ? "info" : "danger"}>{c.health}</Badge> : "—"}</Td>
                  <Td className="tabular-nums">{bytes(cont.stats[c.name]?.memory_bytes)}</Td><Td className="tabular-nums">{num(cont.stats[c.name]?.cpu_percent)} %</Td>
                  <Td className="text-xs text-muted">{c.started_at && ago(c.started_at, lang)}</Td>
                  <Td><Button size="sm" variant="ghost" onClick={async () => { const r = await api(`/api/v1/engines/${c.engine ?? c.service}/logs?tail=300`); setLogs({ name: c.name, text: r.logs }); }}>logs</Button></Td></tr>
              ))}
            </Table>
          )}
        </Card>
      )}
      <Card className="mt-4">
        <CardHeader title={t("dash.alerts")} />
        {!alerts?.length ? <Empty title={t("dash.no_alerts")} /> : (
          <Table head={["", "tipo", "mensaje", "primera vez", "última", "resuelta", ""]}>
            {alerts.map((a: any) => <tr key={a.id}><Td><Badge tone={a.resolved_at ? "muted" : a.severity === "critical" ? "danger" : "warn"}>{a.severity}</Badge></Td><Td>{a.kind}</Td><Td>{a.message}</Td><Td className="text-xs text-muted">{dt(a.first_seen, lang)}</Td><Td className="text-xs text-muted">{dt(a.last_seen, lang)}</Td><Td className="text-xs">{a.resolved_at ? dt(a.resolved_at, lang) : "—"}</Td>
              <Td>{me?.role === "admin" && !a.resolved_at && !a.acknowledged && <Button size="sm" variant="ghost" onClick={async () => { await api(`/api/v1/metrics/alerts/${a.id}/ack`, { method: "POST" }); mutAlerts(); }}>ack</Button>}</Td></tr>)}
          </Table>
        )}
      </Card>
      {logs && <Modal open wide onOpenChange={(o) => !o && setLogs(null)} title={`logs · ${logs.name}`}><pre className="max-h-[70vh] overflow-auto rounded-xl bg-bg p-3 font-mono text-[10px] leading-relaxed">{logs.text || "—"}</pre></Modal>}
    </div>
  );
}
