"use client";
import { HardDrive, Trash2 } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { Bars } from "@/components/charts";
import { Guide } from "@/components/guide";
import { Badge, Button, Card, CardBody, CardHeader, Confirm, Notice, PageHeader, Progress, Skeleton, Stat, Table, Td } from "@/components/ui";
import { api } from "@/lib/api";
import { bytes } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { useMe, useToast } from "@/lib/providers";

export default function StoragePage() {
  const { t } = useI18n();
  const { me } = useMe();
  const { push } = useToast();
  const { data } = useSWR("/api/v1/storage", { refreshInterval: 30000 });
  const { data: cache, mutate } = useSWR(me?.role === "admin" ? "/api/v1/storage/hf-cache" : null);
  const [gc, setGc] = useState(false);
  if (!data) return <Skeleton className="h-64" />;
  const fs = data.filesystem;
  const usedPct = (fs.used_bytes / fs.total_bytes) * 100;
  const orphans = (cache?.revisions ?? []).filter((r: any) => r.orphan);
  return (
    <div>
      <PageHeader title={t("storage.title")} subtitle={t("storage.subtitle")} icon={<HardDrive className="h-5 w-5" />} />
      <Guide id="storage" />
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat label={`${fs.mount}`} value={`${usedPct.toFixed(1)} %`} sub={`${bytes(fs.used_bytes)} / ${bytes(fs.total_bytes)}`} icon={<HardDrive className="h-4 w-4" />} />
        <Stat label="Libre" value={bytes(fs.free_bytes)} sub={`umbral de seguridad ${data.min_free_gb} GB`} />
        <Stat label="Disponible para descargas" value={bytes(Math.max(0, data.available_for_downloads_bytes))} sub={`reservado ${bytes(data.reserved_for_downloads_bytes)}`} />
        <Stat label="Mi uso" value={bytes(data.my_usage_bytes)} sub={`cuota ${data.my_quota_gb} GB`} />
      </div>
      <Card className="mt-4"><CardBody><Progress value={usedPct} tone={usedPct > data.warn_percent ? "warn" : "accent"} /><p className="mt-2 text-xs text-muted">Un único NVMe (ext4). Los datos de usuario nunca se eliminan automáticamente para liberar espacio.</p></CardBody></Card>
      {me?.role === "admin" && data.categories && (
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <Card><CardHeader title="Por categoría" subtitle={data.note} /><CardBody><Bars data={Object.entries(data.categories).map(([k, v]: any) => ({ name: k, GB: +(v / 1e9).toFixed(2) }))} xKey="name" bars={[{ key: "GB", name: "GB" }]} unit="" /></CardBody></Card>
          <Card><CardHeader title="Por usuario" /><Table head={["usuario", "datasets", "artefactos", "cuota"]}>{data.per_user.map((u: any) => <tr key={u.user_id}><Td>{u.username}</Td><Td>{bytes(u.datasets_bytes)}</Td><Td>{bytes(u.artifacts_bytes)}</Td><Td>{u.quota_gb} GB</Td></tr>)}</Table></Card>
          <Card className="xl:col-span-2"><CardHeader title="Caché de Hugging Face" subtitle={cache ? bytes(cache.total_size) : ""} actions={orphans.length > 0 && <Button size="sm" variant="danger" onClick={() => setGc(true)}><Trash2 className="h-3.5 w-3.5" />Limpiar {orphans.length} huérfanas</Button>} />
            <Table head={["repo", "revisión", t("common.size"), t("common.status")]}>{(cache?.revisions ?? []).map((r: any) => <tr key={r.repo_id + r.revision}><Td>{r.repo_id}</Td><Td className="font-mono text-xs">{r.revision.slice(0, 10)}</Td><Td>{bytes(r.size)}</Td><Td>{r.registered ? <Badge tone="ok">registrado</Badge> : r.download_task ? <Badge tone="info">descarga</Badge> : <Badge tone="warn">huérfano</Badge>}</Td></tr>)}</Table>
          </Card>
        </div>
      )}
      <Notice tone="muted"><span className="mt-4 block">Las copias de seguridad locales viven en el mismo disco: configura un destino externo en Ajustes → Copias de seguridad.</span></Notice>
      <Confirm open={gc} onOpenChange={setGc} danger title="Limpiar caché" text={`Se eliminarán ${orphans.length} revisiones no registradas ni en descarga (${bytes(orphans.reduce((s: number, r: any) => s + r.size, 0))}).`}
        onConfirm={async () => { const r = await api("/api/v1/storage/hf-cache/gc", { method: "POST", json: { revisions: orphans.map((o: any) => o.revision) } }); push("ok", `${bytes(r.freed)} liberados`); setGc(false); mutate(); }} />
    </div>
  );
}
