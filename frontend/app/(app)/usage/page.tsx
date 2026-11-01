"use client";
import { Gauge } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { Bars, TimeChart } from "@/components/charts";
import { Guide } from "@/components/guide";
import { Card, CardBody, CardHeader, Notice, PageHeader, Select, Skeleton, Td, Table } from "@/components/ui";
import { bytes, num } from "@/lib/format";
import { useL } from "@/lib/i18n";
import { isManager, useMe } from "@/lib/providers";

const kfmt = (n: number) => (n >= 1e6 ? `${(n / 1e6).toFixed(1)} M` : n >= 1e3 ? `${(n / 1e3).toFixed(1)} k` : String(n));

export default function UsagePage() {
  const L = useL();
  const { me } = useMe();
  const [days, setDays] = useState(30);
  const [scope, setScope] = useState("me");
  const { data: u } = useSWR(`/api/v1/usage?days=${days}&scope=${scope}`);
  const SRC: Record<string, string> = { playground: L("Chat", "Chat"), api: L("API y herramientas", "API and tools"), task: L("Tareas programadas", "Scheduled tasks"), benchmark: "Benchmarks" };
  return (
    <div>
      <PageHeader title={L("Uso", "Usage")} subtitle={L("Cuánto usas el Spark: tokens, modelos, proyectos, trabajos de GPU y almacenamiento", "How much you use the Spark: tokens, models, projects, GPU jobs and storage")} icon={<Gauge className="h-5 w-5" />}
        actions={<div className="flex gap-2">
          {isManager(me) && <Select className="w-auto" value={scope} onChange={(e) => setScope(e.target.value)}><option value="me">{L("Mi uso", "My usage")}</option><option value="all">{L("Todo el equipo", "Everyone")}</option></Select>}
          <Select className="w-auto" value={days} onChange={(e) => setDays(Number(e.target.value))}><option value={7}>{L("7 días", "7 days")}</option><option value={30}>{L("30 días", "30 days")}</option><option value={90}>{L("90 días", "90 days")}</option><option value={365}>{L("1 año", "1 year")}</option></Select>
        </div>} />
      <Guide id="usage" />
      {!u ? <Skeleton className="h-64" /> : (
        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {[[L("Peticiones", "Requests"), num(u.totals.requests, 0)], [L("Tokens leídos", "Input tokens"), kfmt(u.totals.prompt_tokens)], [L("Tokens generados", "Output tokens"), kfmt(u.totals.completion_tokens)],
              [L("Tokens este mes", "Tokens this month"), kfmt(u.totals.month_tokens) + (u.quota?.monthly_tokens ? ` / ${kfmt(u.quota.monthly_tokens)}` : "")]].map(([k, v]) => (
              <Card key={k as string} className="p-4"><p className="text-xs text-muted">{k}</p><p className="mt-1 text-2xl font-semibold tabular-nums">{v}</p></Card>))}
          </div>
          {u.storage && <Card className="p-4"><div className="mb-1 flex justify-between text-xs text-muted"><span>{L("Almacenamiento (archivos, imágenes, datos)", "Storage (files, images, data)")}</span><span>{bytes(u.storage.used_bytes)} / {u.storage.quota_gb} GB</span></div>
            <div className="h-2 overflow-hidden rounded bg-elevated"><div className="h-full bg-accent" style={{ width: `${Math.min(100, (u.storage.used_bytes / (u.storage.quota_gb * 1024 ** 3)) * 100)}%` }} /></div></Card>}
          <Card><CardHeader title={L("Tokens por día (miles)", "Tokens per day (thousands)")} /><CardBody>
            {u.daily.length ? <TimeChart data={u.daily.map((d: any) => ({ ...d, prompt: Math.round(d.prompt / 100) / 10, completion: Math.round(d.completion / 100) / 10 }))} xKey="day" lines={[{ key: "prompt", name: L("Leídos", "Input") }, { key: "completion", name: L("Generados", "Output") }]} /> : <Notice tone="muted">{L("Sin uso en este periodo", "No usage in this period")}</Notice>}
          </CardBody></Card>
          <div className="grid gap-4 lg:grid-cols-2">
            <Card><CardHeader title={L("Por modelo", "By model")} /><CardBody>{u.by_model.length ? <Bars data={u.by_model.map((x: any) => ({ ...x, tokens: Math.round(x.tokens / 1000) }))} xKey="model" bars={[{ key: "tokens", name: L("Miles de tokens", "Thousand tokens") }]} /> : "—"}</CardBody></Card>
            <Card><CardHeader title={L("Por origen", "By source")} /><CardBody>{u.by_source.length ? <Bars data={u.by_source.map((x: any) => ({ ...x, source: SRC[x.source] ?? x.source, tokens: Math.round(x.tokens / 1000) }))} xKey="source" bars={[{ key: "tokens", name: L("Miles de tokens", "Thousand tokens") }]} /> : "—"}</CardBody></Card>
            <Card><CardHeader title={L("Chats por proyecto", "Chats by project")} /><Table head={[L("Proyecto", "Project"), L("Respuestas", "Answers"), "Tokens"]}>
              {u.by_project.map((p: any) => <tr key={p.project}><Td>{p.project}</Td><Td>{p.answers}</Td><Td className="tabular-nums">{kfmt(p.tokens)}</Td></tr>)}</Table></Card>
            <Card><CardHeader title={L("Trabajos de GPU y procesos", "GPU jobs and processing")} /><Table head={[L("Tipo", "Type"), L("Trabajos", "Jobs"), L("Minutos", "Minutes")]}>
              {u.jobs.map((j: any) => <tr key={j.group}><Td>{j.group}</Td><Td>{j.count}</Td><Td className="tabular-nums">{num(j.minutes, 1)}</Td></tr>)}</Table></Card>
          </div>
          {u.users && <Card><CardHeader title={L("Por persona", "By person")} subtitle={L("Solo metadatos de uso: nadie ve el contenido de los chats.", "Usage metadata only: nobody sees chat content.")} />
            <Table head={[L("Usuario", "User"), L("Peticiones", "Requests"), "Tokens"]}>
              {u.users.map((x: any) => <tr key={x.user_id ?? "none"}><Td>{x.display_name} <span className="text-xs text-muted">@{x.username}</span></Td><Td>{x.requests}</Td><Td className="tabular-nums">{kfmt(x.tokens)}</Td></tr>)}</Table></Card>}
        </div>
      )}
    </div>
  );
}
