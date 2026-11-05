"use client";
import { Code2, KeyRound, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { Bars } from "@/components/charts";
import { ConnectSteps } from "@/components/connect";
import { Guide } from "@/components/guide";
import { Badge, Button, Card, CardBody, CardHeader, Code, Empty, ErrorBox, Field, Input, Modal, Notice, PageHeader, Select, StatusBadge, Table, Tabs, Td, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { dt } from "@/lib/format";
import { useI18n, useL, tr } from "@/lib/i18n";
import { useToast } from "@/lib/providers";

function Keys() {
  const { t, lang } = useI18n();
  const L = useL();
  const { push } = useToast();
  const { data, mutate } = useSWR("/api/v1/api-keys");
  const [open, setOpen] = useState(false);
  const [f, setF] = useState({ name: "", expires_in_days: 90, scopes: ["inference"], rpm_limit: "" as any });
  const [created, setCreated] = useState<string | null>(null);
  const [err, setErr] = useState<any>(null);
  return (
    <div className="space-y-4">
      <div className="flex justify-end"><Button variant="primary" onClick={() => { setCreated(null); setOpen(true); }}><KeyRound className="h-4 w-4" />{t("api.new_key")}</Button></div>
      <Card>
        {!data?.length ? <Empty /> : (
          <Table head={[t("common.name"), tr("prefijo", "prefix"), "scopes", t("common.created"), tr("expira", "expires"), tr("último uso", "last used"), t("common.status"), ""]}>
            {data.map((k: any) => (
              <tr key={k.id}><Td className="font-medium">{k.name}</Td><Td className="font-mono text-xs">{k.prefix}…</Td><Td>{k.scopes.map((s: string) => <Badge key={s} className="mr-1">{s}</Badge>)}</Td>
                <Td className="text-xs text-muted">{dt(k.created_at, lang)}</Td><Td className="text-xs text-muted">{dt(k.expires_at, lang)}</Td><Td className="text-xs text-muted">{dt(k.last_used_at, lang)}</Td>
                <Td><StatusBadge state={k.active ? "ok" : "disabled"} label={k.active ? "activa" : k.revoked_at ? "revocada" : "expirada"} /></Td>
                <Td>{k.active && <Button size="sm" variant="ghost" onClick={async () => { await api(`/api/v1/api-keys/${k.id}`, { method: "DELETE" }); push("ok", t("api.revoke")); mutate(); }}><Trash2 className="h-3.5 w-3.5" />{t("api.revoke")}</Button>}</Td></tr>
            ))}
          </Table>
        )}
      </Card>
      <Modal open={open} onOpenChange={setOpen} title={t("api.new_key")} wide={!!created}>
        {created ? (
          <div className="space-y-4"><Notice tone="warn">{t("api.key_once")}</Notice><Code>{created}</Code>
            <div className="rounded-xl border border-border p-4"><p className="mb-3 text-sm font-semibold">{L("¿Dónde la pongo? Elige tu herramienta:", "Where do I put it? Choose your tool:")}</p><ConnectSteps initialKey={created} compact /></div>
            <div className="flex justify-end"><Button onClick={() => setOpen(false)}>{t("common.close")}</Button></div></div>
        ) : (
          <div className="space-y-3">
            <Field label={t("common.name")}><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label={tr("Expira (días)", "Expires (days)")}><Input type="number" min={1} max={730} value={f.expires_in_days} onChange={(e) => setF({ ...f, expires_in_days: Number(e.target.value) })} /></Field>
              <Field label={tr("Límite req/min", "Requests/min limit")} hint={tr("vacío = política de tu rol", "empty = your role's policy")}><Input type="number" value={f.rpm_limit} onChange={(e) => setF({ ...f, rpm_limit: e.target.value })} /></Field>
            </div>
            <Field label="Scopes" tip={tr("inference = /v1 (OpenAI). jobs = API de trabajos asíncronos. Ninguna clave da acceso administrativo.", "inference = /v1 (OpenAI). jobs = asynchronous jobs API. No key gives administrative access.")}>
              <div className="flex gap-4 text-sm">{["inference", "jobs"].map((s) => <label key={s} className="flex items-center gap-1.5"><input type="checkbox" checked={f.scopes.includes(s)} onChange={(e) => setF({ ...f, scopes: e.target.checked ? [...f.scopes, s] : f.scopes.filter((x) => x !== s) })} />{s}</label>)}</div>
            </Field>
            {err && <ErrorBox error={err} />}
            <div className="flex justify-end gap-2"><Button variant="ghost" onClick={() => setOpen(false)}>{t("common.cancel")}</Button>
              <Button variant="primary" disabled={!f.name || !f.scopes.length} onClick={async () => { setErr(null); try { const r = await api("/api/v1/api-keys", { method: "POST", json: { ...f, rpm_limit: f.rpm_limit ? Number(f.rpm_limit) : null } }); setCreated(r.key); mutate(); } catch (e) { setErr(e); } }}>{t("common.create")}</Button></div>
          </div>
        )}
      </Modal>
    </div>
  );
}

function Usage() {
  const { t } = useI18n();
  const { data } = useSWR("/api/v1/api-keys/usage?days=30");
  const byDay: Record<string, any> = {};
  for (const r of data ?? []) {
    const d = r.day.slice(5, 10);
    byDay[d] = byDay[d] || { day: d, requests: 0, tokens: 0, errors: 0 };
    byDay[d].requests += r.requests; byDay[d].tokens += r.prompt_tokens + r.completion_tokens; byDay[d].errors += r.errors;
  }
  return (
    <div className="space-y-4">
      <Card><CardHeader title={t("api.usage")} subtitle={tr("30 días · solo metadatos (sin contenido de prompts)", "30 days · metadata only (no prompt content)")} /><CardBody>{Object.keys(byDay).length ? <Bars data={Object.values(byDay)} xKey="day" bars={[{ key: "requests", name: "requests" }, { key: "errors", name: "errors", color: "rgb(var(--danger))" }]} /> : <Empty />}</CardBody></Card>
      <Card><Table head={["día", t("common.model"), "requests", "prompt tok", "completion tok", "latencia media", "errores"]}>
        {(data ?? []).map((r: any, i: number) => <tr key={i}><Td className="text-xs">{r.day.slice(0, 10)}</Td><Td>{r.model}</Td><Td>{r.requests}</Td><Td>{r.prompt_tokens}</Td><Td>{r.completion_tokens}</Td><Td>{r.avg_latency_ms} ms</Td><Td>{r.errors}</Td></tr>)}
      </Table></Card>
    </div>
  );
}

function Docs() {
  const { t } = useI18n();
  const [base, setBase] = useState(typeof window !== "undefined" ? `${window.location.origin}/v1` : "/v1");
  useEffect(() => { setBase(`${window.location.origin}/v1`); }, []);
  const { data: models } = useSWR("/api/v1/models");
  const m = (models ?? []).find((x: any) => x.capabilities?.includes("chat"))?.alias ?? "MODEL_ALIAS";
  const py = `from openai import OpenAI

client = OpenAI(base_url="${base}", api_key="sah-...")  # tu clave personal

stream = client.chat.completions.create(
    model="${m}",
    messages=[{"role": "user", "content": "Hola, ¿qué es la memoria unificada?"}],
    temperature=0.2, max_tokens=256, stream=True,
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)`;
  const curl = `curl ${base}/chat/completions \\
  -H "Authorization: Bearer $SAH_API_KEY" -H "Content-Type: application/json" \\
  -d '{"model": "${m}", "messages": [{"role": "user", "content": "Hola"}], "max_tokens": 64}'

curl ${base}/models -H "Authorization: Bearer $SAH_API_KEY"`;
  const jobs = `import time, httpx

H = {"Authorization": "Bearer sah-..."}   # clave con scope "jobs"
API = "${base.replace("/v1", "")}/api/v1"

# 1) enviar un trabajo asíncrono (ejemplo: benchmark de un modelo)
r = httpx.post(f"{API}/benchmarks", headers=H, json={"model_id": "<uuid>", "suite": "perf-short-v1"})
job_id = r.json()["job_id"]

# 2) consultar estado
while True:
    j = httpx.get(f"{API}/jobs/{job_id}", headers=H).json()
    print(j["status"], round(j["progress"] * 100), "%", j.get("message"))
    if j["status"] in ("completed", "failed", "cancelled", "interrupted"):
        break
    time.sleep(5)

# 3) recuperar resultados / artefactos
print(j["result"])
for a in j["artifacts"]:
    data = httpx.get(f"{API.replace('/api/v1', '')}{a['url']}", headers=H).content`;
  return (
    <div className="space-y-4">
      <Card><CardHeader title={t("api.base_url")} /><CardBody className="space-y-2"><Code>{base}</Code>
        <p className="text-xs text-muted">Endpoints: <code>GET /v1/models</code>, <code>POST /v1/chat/completions</code> (streaming SSE, tools, visión), <code>POST /v1/embeddings</code>, <code>POST /v1/messages</code> (Anthropic / Claude Code), <code>POST /mcp</code> (MCP). Solo accesible desde el tailnet. Documentación OpenAPI completa: <a className="text-accent underline" href="/api/docs" target="_blank">/api/docs</a>.</p></CardBody></Card>
      <Card><CardHeader title="Python (OpenAI SDK)" /><CardBody><Code>{py}</Code></CardBody></Card>
      <Card><CardHeader title="cURL" /><CardBody><Code>{curl}</Code></CardBody></Card>
      <Card><CardHeader title={tr("Trabajos asíncronos", "Asynchronous jobs")} /><CardBody><Code>{jobs}</Code></CardBody></Card>
    </div>
  );
}

function Tester() {
  const { t } = useI18n();
  const { data: models } = useSWR("/api/v1/models");
  const chat = (models ?? []).filter((x: any) => x.capabilities?.includes("chat"));
  const [key, setKey] = useState("");
  const [model, setModel] = useState("");
  const [prompt, setPrompt] = useState("Di hola en tres idiomas.");
  const [out, setOut] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  return (
    <Card><CardHeader title={t("api.test")} subtitle={tr("La clave solo se usa en esta petición desde tu navegador; no se guarda.", "The key is only used in this request from your browser; it is not stored.")} /><CardBody className="space-y-3">
      <div className="grid gap-3 sm:grid-cols-2"><Field label="API key"><Input type="password" value={key} onChange={(e) => setKey(e.target.value)} placeholder="sah-…" autoComplete="off" /></Field>
        <Field label={t("common.model")}><Select value={model} onChange={(e) => setModel(e.target.value)}><option value="">—</option>{chat.map((m: any) => <option key={m.id}>{m.alias}</option>)}</Select></Field></div>
      <Field label={t("common.prompt")}><Textarea rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} /></Field>
      <Button variant="primary" loading={busy} disabled={!key || !model} onClick={async () => {
        setBusy(true); const t0 = performance.now();
        try { const r = await fetch("/v1/chat/completions", { method: "POST", headers: { Authorization: `Bearer ${key}`, "Content-Type": "application/json" }, body: JSON.stringify({ model, messages: [{ role: "user", content: prompt }], max_tokens: 200 }) });
          setOut({ status: r.status, ms: Math.round(performance.now() - t0), body: await r.json() }); } catch (e: any) { setOut({ error: e.message }); } finally { setBusy(false); }
      }}>{t("common.run")}</Button>
      {out && <pre className="max-h-80 overflow-auto rounded-xl bg-elevated p-3 font-mono text-[11px]">{`HTTP ${out.status} · ${out.ms} ms\n` + JSON.stringify(out.body ?? out.error, null, 2)}</pre>}
    </CardBody></Card>
  );
}

export default function ApiCenter() {
  const { t } = useI18n();
  return (
    <div>
      <PageHeader title={t("api.title")} subtitle={t("api.subtitle")} icon={<Code2 className="h-5 w-5" />} />
      <Guide id="api" />
      <Tabs tabs={[{ id: "keys", label: t("api.keys"), content: <Keys /> }, { id: "docs", label: t("api.examples"), content: <Docs /> }, { id: "test", label: t("api.test"), content: <Tester /> }, { id: "usage", label: t("api.usage"), content: <Usage /> }]} />
    </div>
  );
}
