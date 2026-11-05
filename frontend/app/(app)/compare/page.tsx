"use client";
import { Download, EyeOff, GitCompare, RotateCcw, Trophy } from "lucide-react";
import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import useSWR from "swr";
import { Bars } from "@/components/charts";
import { Guide } from "@/components/guide";
import { Badge, Button, Card, CardBody, CardHeader, Empty, ErrorBox, Field, Input, Notice, PageHeader, StatusBadge, Switch, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { dt, num } from "@/lib/format";
import { useI18n, tr } from "@/lib/i18n";

export default function ComparePage() {
  const { t, lang } = useI18n();
  const { data: models } = useSWR("/api/v1/models");
  const { data: list, mutate: mutList } = useSWR("/api/v1/comparisons", { refreshInterval: 10000 });
  const chat = (models ?? []).filter((m: any) => m.capabilities?.includes("chat") && !["disabled", "failed", "discovered", "downloading"].includes(m.status));
  const [selected, setSelected] = useState<string[]>([]);
  const [prompt, setPrompt] = useState("");
  const [system, setSystem] = useState("");
  const [temp, setTemp] = useState(0);
  const [maxTok, setMaxTok] = useState(512);
  const [blind, setBlind] = useState(false);
  const [current, setCurrent] = useState<string | null>(null);
  const [err, setErr] = useState<any>(null);
  const { data: comp, mutate } = useSWR(current ? `/api/v1/comparisons/${current}` : null, { refreshInterval: (d: any) => (d?.results?.every((r: any) => ["done", "error"].includes(r.status)) ? 0 : 1500) });
  useEffect(() => { if (!current && list?.length) setCurrent(list[0].id); }, [list, current]);
  async function run() {
    setErr(null);
    try {
      const c = await api("/api/v1/comparisons", { method: "POST", json: { prompt, system_prompt: system || undefined, model_ids: selected, temperature: temp, max_tokens: maxTok, blinded: blind } });
      setCurrent(c.id); mutList();
    } catch (e) { setErr(e); }
  }
  const chartData = (comp?.results ?? []).filter((r: any) => r.metrics).map((r: any) => ({ name: r.alias ?? r.label, ttft: r.metrics.ttft_ms, tps: r.metrics.tokens_per_s }));
  return (
    <div>
      <PageHeader title={t("cmp.title")} subtitle={t("cmp.subtitle")} icon={<GitCompare className="h-5 w-5" />} />
      <Guide id="compare" />
      <div className="grid gap-4 xl:grid-cols-[380px_minmax(0,1fr)]">
        <div className="space-y-4">
          <Card>
            <CardHeader title={t("common.prompt")} />
            <CardBody className="space-y-3">
              <div className="space-y-1">{chat.map((m: any) => (
                <label key={m.id} className="flex items-center gap-2 rounded-lg px-2 py-1 text-sm hover:bg-elevated">
                  <input type="checkbox" checked={selected.includes(m.id)} onChange={(e) => setSelected(e.target.checked ? [...selected, m.id] : selected.filter((x) => x !== m.id))} />
                  <span className="flex-1">{m.alias}</span><Badge>{m.backend}</Badge>
                </label>))}
                {chat.length < 2 && <Notice tone="warn">{tr("Se necesitan al menos 2 modelos de chat instalados.", "At least 2 chat models must be installed.")}</Notice>}
              </div>
              <Field label={t("play.system")}><Textarea rows={2} value={system} onChange={(e) => setSystem(e.target.value)} /></Field>
              <Field label={t("common.prompt")}><Textarea rows={5} value={prompt} onChange={(e) => setPrompt(e.target.value)} /></Field>
              <div className="grid grid-cols-2 gap-3">
                <Field label={t("play.temperature")}><Input type="number" step={0.1} min={0} max={2} value={temp} onChange={(e) => setTemp(Number(e.target.value))} /></Field>
                <Field label={t("play.max_tokens")}><Input type="number" value={maxTok} onChange={(e) => setMaxTok(Number(e.target.value))} /></Field>
              </div>
              <Switch checked={blind} onChange={setBlind} label={t("cmp.blinded")} />
              <Notice tone="muted">{tr("Los modelos se ejecutan uno tras otro: los modelos grandes no necesitan estar residentes a la vez.", "Models run one after another: large models do not need to be resident at the same time.")}</Notice>
              {err && <ErrorBox error={err} />}
              <Button variant="primary" className="w-full" disabled={selected.length < 2 || !prompt} onClick={run}>{t("common.run")}</Button>
            </CardBody>
          </Card>
          <Card>
            <CardHeader title={t("media.history")} />
            <ul className="max-h-80 divide-y divide-border/60 overflow-y-auto">
              {list?.map((c: any) => <li key={c.id}><button className={`w-full px-4 py-2 text-left text-sm hover:bg-elevated ${current === c.id ? "bg-accent/10" : ""}`} onClick={() => setCurrent(c.id)}><div className="truncate">{c.title}</div><div className="text-[11px] text-muted">{dt(c.created_at, lang)} · {c.results.length} modelos {c.blinded && "· ciego"}</div></button></li>)}
            </ul>
          </Card>
        </div>
        <div className="space-y-4">
          {!comp ? <Card><Empty icon={<GitCompare className="h-5 w-5" />} /></Card> : (
            <>
              <Card>
                <CardHeader title={comp.title} subtitle={`temp ${comp.settings.temperature} · max ${comp.settings.max_tokens} · seed ${comp.settings.seed}`}
                  actions={<>
                    {comp.blinded && !comp.preference && <Badge tone="warn"><EyeOff className="h-3 w-3" />{t("cmp.blinded")}</Badge>}
                    <Button size="sm" variant="ghost" onClick={async () => { const c = await api(`/api/v1/comparisons/${comp.id}/rerun`, { method: "POST" }); setCurrent(c.id); mutList(); }}><RotateCcw className="h-3.5 w-3.5" />{t("cmp.rerun")}</Button>
                    <a href={`/api/v1/comparisons/${comp.id}/export?fmt=json`}><Button size="sm" variant="ghost"><Download className="h-3.5 w-3.5" />JSON</Button></a>
                    <a href={`/api/v1/comparisons/${comp.id}/export?fmt=csv`}><Button size="sm" variant="ghost"><Download className="h-3.5 w-3.5" />CSV</Button></a>
                  </>} />
                {chartData.length > 0 && <CardBody><Bars data={chartData} xKey="name" bars={[{ key: "ttft", name: "TTFT (ms)" }, { key: "tps", name: "tokens/s" }]} height={200} /></CardBody>}
              </Card>
              <div className="grid gap-4 lg:grid-cols-2">
                {comp.results.map((r: any, i: number) => (
                  <Card key={i} className={comp.preference?.winner_index === i ? "ring-2 ring-accent" : ""}>
                    <CardHeader title={r.alias ?? r.label ?? `#${i + 1}`} subtitle={r.metrics ? `TTFT ${num(r.metrics.ttft_ms, 0)} ms · ${num(r.metrics.tokens_per_s)} tok/s · ${r.metrics.completion_tokens} tok · ${num(r.metrics.latency_ms / 1000, 2)} s${r.load_seconds ? ` · carga ${num(r.load_seconds)} s` : ""}` : r.backend}
                      actions={<><StatusBadge state={r.status === "done" ? "completed" : r.status === "error" ? "failed" : "running"} />
                        {r.status === "done" && <Button size="sm" variant={comp.preference?.winner_index === i ? "primary" : "outline"} onClick={async () => { await api(`/api/v1/comparisons/${comp.id}/preference`, { method: "POST", json: { winner_index: i } }); mutate(); }}><Trophy className="h-3.5 w-3.5" />{t("cmp.prefer")}</Button>}</>} />
                    <CardBody className="prose-chat max-h-[480px] overflow-y-auto text-sm">
                      {r.error ? <p className="text-danger">{r.error}</p> : r.output ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{r.output}</ReactMarkdown> : <p className="text-muted">…</p>}
                    </CardBody>
                  </Card>
                ))}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
