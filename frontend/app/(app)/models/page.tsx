"use client";
import { Boxes, Download, Pause, Pin, RefreshCw, Star, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { Guide } from "@/components/guide";
import { MainModelCard, ModelLoadButtons, useMain } from "@/components/main-model";
import { MemoryPanel, useMemory } from "@/components/memory";
import { Badge, Button, Card, Empty, ErrorBox, Notice, PageHeader, Progress, Select, Skeleton, StatusBadge, Table, Tabs, Td } from "@/components/ui";
import { api } from "@/lib/api";
import { ago, bytes, num, params, rate } from "@/lib/format";
import { useI18n, tr } from "@/lib/i18n";
import { useMe, useToast } from "@/lib/providers";

function ModelsTab() {
  const { t, lang } = useI18n();
  const { me, setPrefs } = useMe();
  const { push } = useToast();
  const [modality, setModality] = useState("");
  const { data, error, isLoading, mutate } = useSWR(`/api/v1/models${modality ? `?modality=${modality}` : ""}`, { refreshInterval: 5000 });
  const [busy, setBusy] = useState<string | null>(null);
  const favs: string[] = me?.preferences?.favorite_models ?? [];
  const { main, refreshMain } = useMain();
  const { mem } = useMemory();
  async function act(id: string, path: string, label: string) {
    setBusy(id + path);
    try {
      await api(`/api/v1/models/${id}/${path}`, { method: "POST" });
      push("ok", label);
      mutate();
    } catch (e: any) {
      push("error", e.message);
    } finally {
      setBusy(null);
    }
  }
  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Select className="w-48" value={modality} onChange={(e) => setModality(e.target.value)} aria-label="modality">
          <option value="">{t("common.all")}</option>{["text", "embedding", "image", "video", "audio", "scientific"].map((m) => <option key={m}>{m}</option>)}
        </Select>
        <Button variant="ghost" size="sm" onClick={() => mutate()}><RefreshCw className="h-3.5 w-3.5" />{t("common.refresh")}</Button>
      </div>
      {error && <ErrorBox error={error} />}
      {isLoading && <Skeleton className="h-40" />}
      {data && data.length === 0 && <Card><Empty hint={tr("Instala modelos desde «Descubrir modelos»", "Install models from “Discover models”")} action={<Link href="/discover"><Button variant="primary" size="sm">{t("nav.discover")}</Button></Link>} /></Card>}
      {data && data.length > 0 && (
        <Card>
          <Table head={["", t("common.model"), t("common.status"), t("models.backend"), t("models.memory"), t("common.size"), t("common.visibility"), t("common.actions")]}>
            {data.map((m: any) => {
              const inst = mem?.instances?.find((i: any) => i.model_id === m.id);
              const loaded = !!m.runtime;
              const fav = favs.includes(m.id);
              return (
                <tr key={m.id} className="hover:bg-elevated/40">
                  <Td><button aria-label={t("models.favorite")} onClick={() => setPrefs({ favorite_models: fav ? favs.filter((x) => x !== m.id) : [...favs, m.id] })}><Star className={`h-4 w-4 ${fav ? "fill-warn text-warn" : "text-subtle"}`} /></button></Td>
                  <Td>
                    <Link href={`/models/${m.id}`} className="font-medium hover:text-accent">{m.alias}</Link>
                    <div className="text-[11px] text-muted">{m.repo_id ?? m.engine_ref ?? m.display_name} {m.quantization && `· ${m.quantization}`} {m.parameters_b ? `· ${params(m.parameters_b)}` : ""}</div>
                  </Td>
                  <Td><div className="flex flex-col gap-1"><StatusBadge state={m.status} />{m.compat_summary && <StatusBadge state={m.compat_summary} />}</div>{m.status_message && <div className="mt-1 max-w-[220px] truncate text-[11px] text-danger" title={m.status_message}>{m.status_message}</div>}</Td>
                  <Td><Badge>{m.backend}</Badge>{m.pinned && <Badge tone="accent" className="ml-1"><Pin className="h-3 w-3" /></Badge>}</Td>
                  <Td className="text-xs tabular-nums">{inst && ["ready", "busy"].includes(inst.state) ? `${num(inst.measured_gb ?? inst.reserved_gb)} GB ${inst.measured_gb ? t("common.measured") : ""}` : m.runtime?.measured_memory_gb ? `${num(m.runtime.measured_memory_gb)} GB ${t("common.measured")}` : m.measured_memory_gb ? `${num(m.measured_memory_gb)} GB ${t("common.measured")}` : m.estimated_memory_gb ? `~${num(m.estimated_memory_gb)} GB ${t("common.estimated")}` : "—"}
                    {loaded && <div className="text-[11px] text-muted">{ago(m.runtime.last_used_at, lang)}</div>}</Td>
                  <Td className="text-xs tabular-nums">{bytes(m.size_bytes)}</Td>
                  <Td><Badge>{t(`common.${m.visibility}`)}</Badge></Td>
                  <Td>
                    <div className="flex flex-wrap gap-1">
                      <ModelLoadButtons m={m} mainAlias={main?.alias} onChanged={() => { mutate(); refreshMain(); }} />
                      {["installed", "ready"].includes(m.status) && <Button size="sm" variant="ghost" loading={busy === m.id + "smoke-test"} onClick={() => act(m.id, "smoke-test", t("models.smoke"))}>{t("models.smoke")}</Button>}
                    </div>
                  </Td>
                </tr>
              );
            })}
          </Table>
        </Card>
      )}
    </div>
  );
}

function DownloadsTab() {
  const { t, lang } = useI18n();
  const { me } = useMe();
  const { push } = useToast();
  const { data, mutate } = useSWR("/api/v1/downloads", { refreshInterval: 3000 });
  async function act(id: string, a: string) {
    try { await api(`/api/v1/downloads/${id}/${a}`, { method: "POST" }); mutate(); } catch (e: any) { push("error", e.message); }
  }
  if (!data) return <Skeleton className="h-32" />;
  if (data.length === 0) return <Card><Empty /></Card>;
  return (
    <div className="space-y-3">
      {data.map((d: any) => {
        const frac = d.total_bytes ? (d.downloaded_bytes / d.total_bytes) * 100 : 0;
        return (
          <Card key={d.id} className="p-4">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{d.repo_id}</span><Badge>{d.kind}</Badge>
              {d.awaiting_approval ? <Badge tone="warn">{t("models.awaiting")}</Badge> : <StatusBadge state={d.status} />}
              <span className="text-xs text-muted">{d.alias} · {ago(d.created_at, lang)}</span>
              <div className="ml-auto flex gap-1">
                {d.awaiting_approval && (me?.role === "admin" || me?.role === "manager") && <Button size="sm" variant="primary" onClick={() => act(d.id, "approve")}>{t("models.approve")}</Button>}
                {d.status === "running" && d.kind === "hf" && <Button size="sm" variant="outline" onClick={() => act(d.id, "pause")}><Pause className="h-3.5 w-3.5" />{t("models.pause")}</Button>}
                {["paused", "failed", "cancelled"].includes(d.status) && !d.awaiting_approval && <Button size="sm" variant="outline" onClick={() => act(d.id, "resume")}><Download className="h-3.5 w-3.5" />{t("models.resume")}</Button>}
                {["running", "pending", "paused"].includes(d.status) && <Button size="sm" variant="ghost" onClick={() => act(d.id, "cancel")}><X className="h-3.5 w-3.5" />{t("common.cancel")}</Button>}
              </div>
            </div>
            <div className="mt-3 flex items-center gap-3">
              <Progress value={d.status === "completed" ? 100 : frac} tone={d.status === "failed" ? "danger" : d.status === "completed" ? "ok" : "accent"} />
              <span className="w-56 shrink-0 text-right text-xs tabular-nums text-muted">{bytes(d.downloaded_bytes)} / {bytes(d.total_bytes)} {d.status === "running" && `· ${rate(d.speed_bps)}`}</span>
            </div>
            {d.verification && <p className="mt-2 text-[11px] text-muted">Integridad: {d.verification.ok ? "✔" : "✘"} {d.verification.sha256_checked != null ? `${d.verification.sha256_checked} sha256 verificados` : d.verification.method}</p>}
            {d.error && <p className="mt-2 text-xs text-danger">{d.error}</p>}
          </Card>
        );
      })}
    </div>
  );
}

function ImportTab() {
  const { t } = useI18n();
  const { push } = useToast();
  const { data, mutate } = useSWR("/api/v1/models/import/candidates");
  if (!data) return <Skeleton className="h-32" />;
  return (
    <div className="space-y-4">
      <Notice tone="info">{tr("Modelos presentes en Ollama o en cachés de Hugging Face que aún no están registrados. Los modelos de la caché del usuario se muestran solo como referencia (montaje de solo lectura).", "Models found in Ollama or Hugging Face caches that are not registered yet. Models in the user's cache are shown for reference only (read-only mount).")}</Notice>
      <Card>
        <Table head={["Ollama", t("common.size"), ""]}>
          {data.ollama.length === 0 && <tr><Td className="text-muted">—</Td></tr>}
          {data.ollama.map((m: any) => (
            <tr key={m.name}><Td>{m.name}</Td><Td>{bytes(m.size)}</Td>
              <Td><Button size="sm" onClick={async () => { try { await api("/api/v1/models/import/ollama", { method: "POST", json: { name: m.name } }); push("ok", m.name); mutate(); } catch (e: any) { push("error", e.message); } }}>{t("models.import")}</Button></Td></tr>
          ))}
        </Table>
      </Card>
      <Card>
        <Table head={["Hugging Face cache", "revision", t("common.size"), "cache"]}>
          {data.hf_cache.length === 0 && <tr><Td className="text-muted">—</Td></tr>}
          {data.hf_cache.map((m: any) => (
            <tr key={m.repo_id + m.revision}><Td><Link className="hover:text-accent" href={`/discover/${m.repo_id}`}>{m.repo_id}</Link></Td><Td className="font-mono text-xs">{m.revision.slice(0, 10)}</Td><Td>{bytes(m.size)}</Td><Td className="text-xs text-muted">{m.needs_copy ? tr("usuario (ro)", "user (ro)") : "hub"}</Td></tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}

export default function LibraryPage() {
  const { t } = useI18n();
  const { me } = useMe();
  const tabs = [
    { id: "models", label: t("models.title"), content: <ModelsTab /> },
    { id: "downloads", label: t("models.downloads"), content: <DownloadsTab /> },
  ];
  if (me?.role === "admin") tabs.push({ id: "import", label: t("models.import"), content: <ImportTab /> });
  return (
    <div>
      <PageHeader title={t("models.title")} subtitle={t("models.subtitle")} icon={<Boxes className="h-5 w-5" />} />
      <Guide id="library" />
      <MainModelCard />
      <div className="mb-5"><MemoryPanel /></div>
      <Tabs tabs={tabs} />
    </div>
  );
}
