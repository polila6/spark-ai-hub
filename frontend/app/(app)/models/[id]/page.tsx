"use client";
import { ArrowLeft, Boxes, Gauge, ShieldCheck, Trash2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useEffect, useState } from "react";
import useSWR from "swr";
import { Badge, Button, Card, CardBody, CardHeader, Confirm, ErrorBox, Field, Input, Notice, PageHeader, Select, Skeleton, StatusBadge, Switch, Td, Table } from "@/components/ui";
import { api } from "@/lib/api";
import { bytes, dt, num } from "@/lib/format";
import { useI18n, useL } from "@/lib/i18n";
import { useMe, useToast } from "@/lib/providers";
import { kTok } from "@/components/assistant";
import { ModelLoadButtons, useMain } from "@/components/main-model";

const VLLM_FIELDS: [string, string, string][] = [
  ["max_model_len", "number", "Longitud máxima de contexto (tokens)"],
  ["gpu_memory_utilization", "number", "Fracción de la memoria unificada total que vLLM preasigna (0.05–0.9)"],
  ["max_num_seqs", "number", "Secuencias concurrentes"],
  ["kv_cache_dtype", "text", "auto | fp8"],
  ["tool_call_parser", "text", "hermes | llama3_json | mistral | openai | qwen3_coder | pythonic"],
  ["reasoning_parser", "text", "qwen3 | deepseek_r1 | openai_gptoss"],
];

function ContextCard({ id, onChanged }: { id: string; onChanged: () => void }) {
  const { push } = useToast();
  const L = useL();
  const { data: c, mutate } = useSWR(`/api/v1/models/${id}/context`, { refreshInterval: 15000 });
  const [pick, setPick] = useState<number | null>(null);
  const [spec, setSpec] = useState<number | null>(null);
  const [ask, setAsk] = useState(false);
  if (!c?.supported) return null;
  const sel = pick ?? c.current;
  const specSel = spec ?? c.speculative_tokens;
  const change: any = { ...(sel !== c.current ? { max_model_len: sel } : {}), ...(specSel !== c.speculative_tokens ? { speculative_tokens: specSel } : {}) };
  const dirty = Object.keys(change).length > 0;
  async function apply() {
    try {
      const r = await api(`/api/v1/models/${id}/context`, { method: "POST", json: change });
      push("ok", r.reloading ? L(`Ventana ${kTok(sel)}: recargando el modelo (unos minutos)`, `Window ${kTok(sel)}: reloading the model (a few minutes)`) : L(`Ventana ${kTok(sel)}: se aplica en la próxima carga`, `Window ${kTok(sel)}: applied on the next load`));
      setPick(null); setSpec(null); mutate(); onChanged();
    } catch (e: any) { push("error", e.message); } finally { setAsk(false); }
  }
  return (
    <Card className="mt-4">
      <CardHeader title={<span className="flex items-center gap-2"><Gauge className="h-4 w-4" />{L("Ventana de contexto", "Context window")}</span>}
        subtitle={L("Cuántos tokens (conversación, archivos, código, resultados de herramientas) puede ver el modelo en una sola petición. Agentes de programación como OpenCode o Cline necesitan 128k o más.", "How many tokens (conversation, files, code, tool results) the model can see in one request. Coding agents such as OpenCode or Cline need 128k or more.")} />
      <CardBody className="space-y-3 text-sm">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
          {c.presets.map((p: any) => (
            <button key={p.tokens} type="button" disabled={!c.can_manage} onClick={() => setPick(p.tokens)}
              className={`rounded-xl border px-3 py-2 text-left transition ${sel === p.tokens ? "border-accent bg-accent/10" : "border-border hover:bg-elevated"}`}>
              <div className="flex items-center gap-1 font-semibold tabular-nums">{kTok(p.tokens)}{p.tokens === c.native_max && <span className="text-[10px] font-normal text-muted">{L("máx. nativo", "native max")}</span>}{p.yarn && <Badge tone="warn" className="px-1.5 py-0 text-[9px]">YaRN</Badge>}</div>
              <div className="text-[11px] text-muted">{p.fits_at_once != null ? (p.fits_at_once >= 1 ? L(`${p.fits_at_once} a la vez`, `${p.fits_at_once} at once`) : L("no cabe", "does not fit")) : p.tokens === c.current ? L("actual", "current") : "—"}</div>
            </button>
          ))}
        </div>
        <p className="text-xs text-muted">
          {L("Ahora", "Now")}: <b className="text-fg">{kTok(c.current)}</b>{c.native_max ? <> · {L("máximo del modelo", "model maximum")} <b className="text-fg">{kTok(c.native_max)}</b></> : null}
          {c.kv_cache_tokens ? <> · {L("memoria reservada para contexto", "memory reserved for context")}: <b className="text-fg">{kTok(c.kv_cache_tokens)}</b> {L("tokens repartidos entre todas las conversaciones. «N a la vez» = cuántas conversaciones de ese tamaño completo caben juntas; las cortas ocupan solo lo que usan.", "tokens shared by all conversations. “N at once” = how many full-size conversations fit together; short ones only take what they use.")}</> : null}
        </p>
        {c.presets.some((p: any) => p.yarn) && <p className={`text-xs ${c.native_max && sel > c.native_max ? "text-warn" : "text-muted"}`}><Badge tone="warn" className="mr-1 px-1.5 py-0 text-[9px]">YaRN</Badge>{L("Por encima de la ventana nativa se activa YaRN: permite textos más largos, pero puede rendir algo peor en conversaciones cortas.", "Above the native window YaRN is enabled: longer texts, but slightly worse quality on short conversations.")}</p>}
        {c.kv_cache_tokens && sel > c.kv_cache_tokens && <Notice tone="warn">{L("No cabe ni una conversación de ese tamaño con la memoria reservada: sube «gpu_memory_utilization» en los parámetros avanzados.", "Not even one conversation of that size fits in the reserved memory: raise gpu_memory_utilization in the advanced parameters.")}</Notice>}
        {c.mtp_available && (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border p-3">
            <div><p className="font-medium">{L("Respuestas más rápidas (MTP)", "Faster answers (MTP)")}</p>
              <p className="text-xs text-muted">{L("El modelo trae capas que predicen varios tokens a la vez; vLLM las comprueba y acepta las correctas. Mismo resultado, ~25–30 % más tokens por segundo y casi sin memoria extra.", "The model ships layers that predict several tokens at once; vLLM verifies them. Same output, ~25–30% more tokens per second, almost no extra memory.")}</p></div>
            <Switch checked={specSel > 0} onChange={(v) => c.can_manage && setSpec(v ? 2 : 0)} label={specSel > 0 ? L("Activado", "On") : L("Desactivado", "Off")} />
          </div>
        )}
        {c.can_manage && dirty && (
          <div className="flex flex-wrap items-center justify-end gap-2">
            <span className="text-xs text-muted">{c.loaded ? L("El modelo se recargará (unos 4–5 min sin responder).", "The model will reload (about 4–5 min without answering).") : L("Se aplicará en la próxima carga.", "Applied on the next load.")} {L("Después vuelve a copiar la configuración de tu herramienta en «Conectar».", "Then copy your tool configuration again from “Connect”.")}</span>
            <Button variant="primary" onClick={() => (c.loaded ? setAsk(true) : apply())}>{L("Aplicar", "Apply")}{change.max_model_len ? ` ${kTok(sel)}` : ""}</Button>
          </div>
        )}
      </CardBody>
      <Confirm open={ask} onOpenChange={setAsk} title={L("Aplicar cambios y recargar el modelo", "Apply changes and reload the model")} text={L("El modelo se descarga y se vuelve a cargar con la nueva ventana. Durante unos minutos los chats y las herramientas que lo usan esperarán. ¿Continuar?", "The model is unloaded and loaded again with the new window. For a few minutes chats and tools using it will wait. Continue?")} onConfirm={apply} />
    </Card>
  );
}

export default function ModelPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { t, lang } = useI18n();
  const { me } = useMe();
  const { push } = useToast();
  const router = useRouter();
  const { data: m, error, mutate } = useSWR(`/api/v1/models/${id}`, { refreshInterval: 5000 });
  const { data: users } = useSWR("/api/v1/users/directory");
  const [rp, setRp] = useState<any>({});
  const [check, setCheck] = useState<any>(null);
  const [del, setDel] = useState(false);
  const [busy, setBusy] = useState("");
  const { main, refreshMain } = useMain();
  useEffect(() => { if (m?.runtime_params) setRp(m.runtime_params); }, [m?.runtime_params]);
  if (error) return <ErrorBox error={error} />;
  if (!m) return <Skeleton className="h-64" />;
  const canManage = me?.role === "admin" || m.owner_id === me?.id;
  async function patch(body: any, msg = t("common.save")) {
    try { await api(`/api/v1/models/${id}`, { method: "PATCH", json: body }); push("ok", msg); mutate(); } catch (e: any) { push("error", e.message); }
  }
  async function post(path: string, label: string) {
    setBusy(path);
    try { const r = await api(`/api/v1/models/${id}/${path}`, { method: "POST" }); push("ok", label); mutate(); return r; } catch (e: any) { push("error", e.message); } finally { setBusy(""); }
  }
  return (
    <div>
      <Link href="/models" className="mb-3 inline-flex items-center gap-1 text-xs text-muted hover:text-fg"><ArrowLeft className="h-3 w-3" />{t("models.title")}</Link>
      <PageHeader title={m.alias} subtitle={`${m.repo_id ?? m.engine_ref ?? ""} ${m.revision ? `@ ${m.revision.slice(0, 12)}` : ""}`} icon={<Boxes className="h-5 w-5" />}
        actions={<>
          <StatusBadge state={m.status} />
          <ModelLoadButtons m={m} mainAlias={main?.alias} size="md" onChanged={() => { mutate(); refreshMain(); }} />
          <Button variant="ghost" loading={busy === "check"} onClick={async () => setCheck(await post("check", t("models.check")))}>{t("models.check")}</Button>
          {canManage && <Button variant="ghost" onClick={() => setDel(true)}><Trash2 className="h-4 w-4 text-danger" /></Button>}
        </>} />
      {m.status_message && <div className="mb-4"><Notice tone={m.status === "failed" ? "danger" : "info"}>{m.status_message}</Notice></div>}
      {check && <div className="mb-4"><Notice tone={check.ok ? "ok" : check.checked === false ? "info" : "danger"}>{check.checked === false ? check.reason : `${check.verified}/${check.files} archivos OK · faltan ${check.missing?.length ?? 0} · discrepancias ${check.mismatched?.length ?? 0}`}</Notice></div>}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardHeader title={t("common.details")} />
          <CardBody className="grid gap-3 text-sm sm:grid-cols-2">
            {[["models.backend", m.backend], ["Arquitectura", m.architecture], ["Cuantización", m.quantization], ["Formato", m.format], ["Parámetros", m.parameters_b ? `${num(m.parameters_b, 2)}B` : null],
              ["Contexto", m.context_length], ["Licencia", m.license], [t("common.size"), bytes(m.size_bytes)], ["Memoria medida", m.measured_memory_gb ? `${num(m.measured_memory_gb)} GB` : null],
              ["Memoria estimada", m.estimated_memory_gb ? `${num(m.estimated_memory_gb)} GB` : null], ["Último uso", dt(m.last_used_at, lang)], ["Código remoto", m.custom_code ? (m.trust_remote_code_approved ? "aprobado" : "NO aprobado") : "no"]]
              .map(([k, v]: any) => <div key={k} className="flex justify-between gap-3 border-b border-border/50 pb-2"><span className="text-muted">{k.startsWith("models.") ? t(k) : k}</span><span className="text-right font-medium">{v ?? "—"}</span></div>)}
            <div className="sm:col-span-2 flex flex-wrap gap-1">{m.capabilities.map((c: string) => <Badge key={c} tone="info">{c}</Badge>)}</div>
            {m.smoke_test && <div className="sm:col-span-2"><Notice tone={m.smoke_test.ok ? "ok" : "danger"} icon={<ShieldCheck className="h-4 w-4" />}>
              Prueba rápida {m.smoke_test.ok ? "superada" : "fallida"} · {m.smoke_test.ts && dt(m.smoke_test.ts, lang)} {m.smoke_test.ttft_ms && `· TTFT ${m.smoke_test.ttft_ms} ms`} {m.smoke_test.tokens_per_s && `· ${m.smoke_test.tokens_per_s} tok/s`} {m.smoke_test.load_seconds && `· carga ${m.smoke_test.load_seconds} s`} {m.smoke_test.output && `· «${m.smoke_test.output}»`} {m.smoke_test.error}
            </Notice></div>}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title={t("common.visibility")} />
          <CardBody className="space-y-3">
            <Select disabled={!canManage} value={m.visibility} onChange={(e) => patch({ visibility: e.target.value })}>
              <option value="private">{t("common.private")}</option><option value="shared">{t("common.shared")}</option>{me?.role === "admin" && <option value="public">{t("common.public")}</option>}
            </Select>
            {canManage && m.visibility === "shared" && users && (
              <div className="max-h-40 space-y-1 overflow-y-auto rounded-lg border border-border p-2">
                {users.filter((u: any) => u.id !== me?.id).map((u: any) => {
                  const on = m.shared_with?.includes(u.id);
                  return <label key={u.id} className="flex items-center gap-2 text-sm"><input type="checkbox" checked={!!on} onChange={async () => {
                    const next = on ? m.shared_with.filter((x: string) => x !== u.id) : [...(m.shared_with ?? []), u.id];
                    await api(`/api/v1/models/${id}/shares`, { method: "PUT", json: { user_ids: next } }); mutate();
                  }} />{u.display_name}</label>;
                })}
              </div>
            )}
            {me?.role === "admin" && <Switch checked={m.pinned} onChange={(v) => patch({ pinned: v })} label={t("models.pin")} />}
            {canManage && <Switch checked={m.status === "disabled"} onChange={(v) => patch({ disabled: v })} label={t("state.disabled")} />}
            {me?.role === "admin" && m.custom_code && <Button size="sm" variant={m.trust_remote_code_approved ? "outline" : "danger"} onClick={async () => { await api(`/api/v1/models/${id}/approve-remote-code?approve=${!m.trust_remote_code_approved}`, { method: "POST" }); mutate(); }}>
              {m.trust_remote_code_approved ? "Revocar código remoto" : "Aprobar código remoto (aislado)"}</Button>}
          </CardBody>
        </Card>
      </div>
      {m.backend === "vllm" && <ContextCard id={id} onChanged={mutate} />}
      {canManage && ["vllm", "ollama"].includes(m.backend) && (
        <Card className="mt-4">
          <CardHeader title={t("models.runtime_params")} subtitle={m.backend === "vllm" ? "Se aplica en la próxima carga del modelo." : "keep_alive de Ollama (el planificador descarga modelos inactivos antes)."} />
          <CardBody>
            <div className="grid gap-3 sm:grid-cols-3">
              {(m.backend === "vllm" ? VLLM_FIELDS : [["keep_alive", "text", "p. ej. 60m"]] as any).map(([k, type, tip]: any) => (
                <Field key={k} label={k} tip={tip}><Input type={type} step="any" value={rp[k] ?? ""} onChange={(e) => setRp({ ...rp, [k]: type === "number" ? (e.target.value === "" ? undefined : Number(e.target.value)) : e.target.value || undefined })} /></Field>
              ))}
              {m.backend === "vllm" && <div className="flex items-end gap-4"><Switch checked={!!rp.enable_tool_calling} onChange={(v) => setRp({ ...rp, enable_tool_calling: v })} label="tool calling" /><Switch checked={!!rp.enforce_eager} onChange={(v) => setRp({ ...rp, enforce_eager: v })} label="enforce_eager" /></div>}
            </div>
            <div className="mt-4 flex justify-end"><Button variant="primary" onClick={() => patch({ runtime_params: Object.fromEntries(Object.entries(rp).filter(([, v]) => v !== undefined && v !== "")) })}>{t("common.save")}</Button></div>
          </CardBody>
        </Card>
      )}
      <Card className="mt-4">
        <CardHeader title={t("models.history")} />
        <Table head={["", "evento", "de → a", "detalles"]}>
          {m.history.map((h: any, i: number) => <tr key={i}><Td className="whitespace-nowrap text-xs text-muted">{dt(h.ts, lang)}</Td><Td className="text-xs font-medium">{h.event}</Td><Td className="text-xs">{h.from ?? ""} → {h.to ?? ""}</Td><Td className="max-w-md truncate font-mono text-[10px] text-muted" >{JSON.stringify(h.details)}</Td></tr>)}
        </Table>
      </Card>
      <Confirm open={del} onOpenChange={setDel} danger title={t("common.delete")} text={`${t("models.delete_confirm")} ${m.dependencies?.active_jobs?.length ? `(${m.dependencies.active_jobs.length} trabajos activos lo usan: no se podrá)` : ""}`}
        onConfirm={async () => { try { await api(`/api/v1/models/${id}`, { method: "DELETE" }); router.push("/models"); } catch (e: any) { push("error", e.message); setDel(false); } }} />
    </div>
  );
}
