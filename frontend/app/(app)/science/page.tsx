"use client";
import { Database, FlaskConical, Play, ShieldAlert, Trash2, Upload } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { Guide } from "@/components/guide";
import { Badge, Button, Card, CardBody, CardHeader, Empty, ErrorBox, Field, Input, Modal, Notice, PageHeader, Select, StatusBadge, Table, Tabs, Td, Textarea } from "@/components/ui";
import { api, upload } from "@/lib/api";
import { bytes, dt } from "@/lib/format";
import { useI18n, tr } from "@/lib/i18n";
import { useMe, useToast } from "@/lib/providers";

function Datasets() {
  const { t, lang } = useI18n();
  const { me } = useMe();
  const { push } = useToast();
  const { data, mutate } = useSWR("/api/v1/datasets");
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<any>(null);
  return (
    <div className="grid gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
      <Card>
        <CardHeader title={t("sci.upload_dataset")} subtitle={tr("CSV, JSON, JSONL, NPY, NPZ (sin pickle), ZIP de imágenes", "CSV, JSON, JSONL, NPY, NPZ (no pickle), image ZIP")} icon={<Upload className="h-4 w-4" />} />
        <CardBody className="space-y-3">
          <Field label={t("common.name")}><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label={tr("Descripción", "Description")}><Textarea rows={2} value={desc} onChange={(e) => setDesc(e.target.value)} /></Field>
          <input aria-label="file" type="file" accept=".csv,.json,.jsonl,.npy,.npz,.zip" onChange={(e) => setFile(e.target.files?.[0] ?? null)} className="block w-full text-xs text-muted file:mr-3 file:rounded-lg file:border-0 file:bg-elevated file:px-3 file:py-2 file:text-fg" />
          {err && <ErrorBox error={err} />}
          <Button variant="primary" className="w-full" disabled={!file || !name} loading={busy} onClick={async () => {
            setBusy(true); setErr(null);
            try { const fd = new FormData(); fd.append("file", file!); fd.append("name", name); fd.append("description", desc); await upload("/api/v1/datasets", fd); push("ok", name); setName(""); setFile(null); mutate(); }
            catch (e) { setErr(e); } finally { setBusy(false); }
          }}>{t("common.upload")}</Button>
        </CardBody>
      </Card>
      <Card>
        {!data?.length ? <Empty icon={<Database className="h-5 w-5" />} /> : (
          <Table head={[t("common.name"), "formato", t("common.size"), "esquema", t("common.visibility"), t("common.created"), ""]}>
            {data.map((d: any) => (
              <tr key={d.id}>
                <Td><span className="font-medium">{d.name}</span><div className="text-[10px] font-mono text-muted">{d.sha256.slice(0, 12)}</div></Td>
                <Td><Badge>{d.format}</Badge></Td><Td>{bytes(d.size_bytes)}</Td>
                <Td className="max-w-xs truncate text-xs text-muted">{d.schema.columns ? `${d.schema.rows} filas · ${d.schema.columns.join(", ")}` : d.schema.images ? `${d.schema.images} imágenes · ${d.schema.labels_from_folders?.length ?? 0} clases` : JSON.stringify(d.schema)}</Td>
                <Td>{d.owner_id === me?.id ? <Select className="h-8 w-28 text-xs" value={d.visibility} onChange={async (e) => { await api(`/api/v1/datasets/${d.id}`, { method: "PATCH", json: { visibility: e.target.value } }); mutate(); }}><option value="private">{t("common.private")}</option><option value="public">{t("common.public")}</option></Select> : <Badge>{d.visibility}</Badge>}</Td>
                <Td className="text-xs text-muted">{dt(d.created_at, lang)}</Td>
                <Td>{d.owner_id === me?.id && <Button size="sm" variant="ghost" aria-label={t("common.delete")} onClick={async () => { await api(`/api/v1/datasets/${d.id}`, { method: "DELETE" }); mutate(); }}><Trash2 className="h-3.5 w-3.5" /></Button>}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}

function Models() {
  const { t } = useI18n();
  const { push } = useToast();
  const { data: adapters } = useSWR("/api/v1/science/adapters");
  const { data: models, mutate } = useSWR("/api/v1/models?modality=scientific");
  const { data: hfModels } = useSWR("/api/v1/models?backend=");
  const [adapter, setAdapter] = useState("linear_regression");
  const [name, setName] = useState("");
  const [cfg, setCfg] = useState('{\n  "feature_columns": ["x1", "x2", "x3"],\n  "target_names": ["y"]\n}');
  const [files, setFiles] = useState<FileList | null>(null);
  const [err, setErr] = useState<any>(null);
  const [hfSrc, setHfSrc] = useState("");
  const hfCands = (hfModels ?? []).filter((m: any) => m.source === "hf" && ["installed", "downloaded"].includes(m.status));
  return (
    <div className="grid gap-4 xl:grid-cols-[400px_minmax(0,1fr)]">
      <Card>
        <CardHeader title={t("sci.register_model")} icon={<FlaskConical className="h-4 w-4" />} />
        <CardBody className="space-y-3">
          <Notice tone="warn" icon={<ShieldAlert className="h-4 w-4" />}>{t("sci.pickle_warning")}</Notice>
          <Field label={t("sci.adapter")}><Select value={adapter} onChange={(e) => setAdapter(e.target.value)}>{adapters && Object.entries(adapters).map(([k, v]: any) => <option key={k} value={k}>{v.name.es} ({k})</option>)}</Select></Field>
          {adapters?.[adapter] && <p className="text-[11px] text-muted">{adapters[adapter].description} · archivos: {adapters[adapter].required_files.join(", ")} · datasets: {adapters[adapter].dataset_formats.join(", ")}</p>}
          <Field label={t("common.name")}><Input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          <Field label="config.json"><Textarea rows={5} className="font-mono text-xs" value={cfg} onChange={(e) => setCfg(e.target.value)} /></Field>
          <input aria-label="files" type="file" multiple accept=".safetensors,.json,.txt,.md" onChange={(e) => setFiles(e.target.files)} className="block w-full text-xs text-muted file:mr-3 file:rounded-lg file:border-0 file:bg-elevated file:px-3 file:py-2 file:text-fg" />
          {err && <ErrorBox error={err} />}
          <Button variant="primary" className="w-full" disabled={!name || !files?.length} onClick={async () => {
            setErr(null);
            try { const fd = new FormData(); fd.append("name", name); fd.append("adapter", adapter); fd.append("config_json", cfg); Array.from(files!).forEach((f) => fd.append("files", f)); await upload("/api/v1/science/models", fd); push("ok", name); mutate(); }
            catch (e) { setErr(e); }
          }}>{t("sci.register_model")}</Button>
          <div className="border-t border-border pt-3">
            <p className="mb-2 text-xs font-medium text-muted">Desde un snapshot HF descargado (p. ej. timm/resnet18.a1_in1k → image_classifier)</p>
            <div className="flex gap-2">
              <Select value={hfSrc} onChange={(e) => setHfSrc(e.target.value)}><option value="">—</option>{hfCands.map((m: any) => <option key={m.id} value={m.id}>{m.repo_id}</option>)}</Select>
              <Button disabled={!hfSrc || !name} onClick={async () => {
                setErr(null);
                try { await api("/api/v1/science/models/from-hf", { method: "POST", json: { source_model_id: hfSrc, name, adapter, config: adapter === "image_classifier" ? {} : JSON.parse(cfg) } }); push("ok", name); mutate(); } catch (e) { setErr(e); }
              }}>{t("common.create")}</Button>
            </div>
          </div>
        </CardBody>
      </Card>
      <Card>
        {!models?.length ? <Empty /> : (
          <Table head={[t("common.name"), t("sci.adapter"), tr("parámetros", "parameters"), t("common.size"), t("common.status"), t("common.visibility")]}>
            {models.map((m: any) => <tr key={m.id}><Td><Link className="font-medium hover:text-accent" href={`/models/${m.id}`}>{m.alias}</Link><div className="text-[10px] text-muted">{m.repo_id}</div></Td><Td><Badge tone="info">{m.task}</Badge></Td><Td className="tabular-nums">{m.parameters_b != null ? (m.parameters_b * 1e9).toLocaleString() : "—"}</Td><Td>{bytes(m.size_bytes)}</Td><Td><StatusBadge state={m.status} /></Td><Td><Badge>{m.visibility}</Badge></Td></tr>)}
          </Table>
        )}
      </Card>
    </div>
  );
}

function Experiments() {
  const { t, lang } = useI18n();
  const { push } = useToast();
  const { data: exps, mutate } = useSWR("/api/v1/experiments", { refreshInterval: 4000 });
  const { data: models } = useSWR("/api/v1/models?modality=scientific");
  const { data: ds } = useSWR("/api/v1/datasets");
  const [open, setOpen] = useState(false);
  const [f, setF] = useState<any>({ name: "", description: "", model_id: "", dataset_id: "", config: "{}" });
  const [err, setErr] = useState<any>(null);
  return (
    <div>
      <div className="mb-3 flex justify-end"><Button variant="primary" onClick={() => setOpen(true)}>{t("sci.new_experiment")}</Button></div>
      {!exps?.length ? <Card><Empty /></Card> : (
        <div className="grid gap-3 lg:grid-cols-2">
          {exps.map((e: any) => {
            const runs = Object.entries(e.metrics ?? {}) as [string, any][];
            const last = runs[runs.length - 1]?.[1];
            return (
              <Card key={e.id} className="p-4">
                <div className="flex items-center gap-2"><span className="font-medium">{e.name}</span><StatusBadge state={e.status === "draft" ? "pending" : e.status} />
                  <Button size="sm" variant="outline" className="ml-auto" onClick={async () => { try { const r = await api(`/api/v1/experiments/${e.id}/run`, { method: "POST", json: {} }); push("ok", `job ${r.job_id.slice(0, 8)}`); mutate(); } catch (x: any) { push("error", x.message); } }}><Play className="h-3.5 w-3.5" />{t("common.run")}</Button></div>
                <p className="mt-1 text-xs text-muted">{e.description}</p>
                <p className="mt-1 text-[11px] text-subtle">{models?.find((m: any) => m.id === e.model_id)?.alias} · {ds?.find((d: any) => d.id === e.dataset_id)?.name} · {dt(e.updated_at, lang)} · {e.job_ids.length} ejecuciones</p>
                {last && <pre className="mt-2 max-h-48 overflow-auto rounded-lg bg-elevated p-2 font-mono text-[10px]">{JSON.stringify(last, null, 2)}</pre>}
                <div className="mt-2 flex flex-wrap gap-2 text-xs">{e.job_ids.slice(-3).map((j: string) => <Link key={j} className="text-accent hover:underline" href={`/jobs?open=${j}`}>job {j.slice(0, 8)} →</Link>)}</div>
              </Card>
            );
          })}
        </div>
      )}
      <Modal open={open} onOpenChange={setOpen} title={t("sci.new_experiment")}>
        <div className="space-y-3">
          <Field label={t("common.name")}><Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
          <Field label={tr("Descripción", "Description")}><Textarea rows={2} value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} /></Field>
          <Field label={t("common.model")}><Select value={f.model_id} onChange={(e) => setF({ ...f, model_id: e.target.value })}><option value="">—</option>{models?.map((m: any) => <option key={m.id} value={m.id}>{m.alias} ({m.task})</option>)}</Select></Field>
          <Field label="Dataset"><Select value={f.dataset_id} onChange={(e) => setF({ ...f, dataset_id: e.target.value })}><option value="">—</option>{ds?.map((d: any) => <option key={d.id} value={d.id}>{d.name} ({d.format})</option>)}</Select></Field>
          <Field label="Opciones (JSON)" hint='p. ej. {"batch_size": 32}'><Textarea rows={2} className="font-mono text-xs" value={f.config} onChange={(e) => setF({ ...f, config: e.target.value })} /></Field>
          {err && <ErrorBox error={err} />}
          <div className="flex justify-end gap-2"><Button variant="ghost" onClick={() => setOpen(false)}>{t("common.cancel")}</Button>
            <Button variant="primary" onClick={async () => { setErr(null); try { await api("/api/v1/experiments", { method: "POST", json: { ...f, model_id: f.model_id || null, dataset_id: f.dataset_id || null, config: JSON.parse(f.config || "{}") } }); setOpen(false); mutate(); } catch (e) { setErr(e); } }}>{t("common.create")}</Button></div>
        </div>
      </Modal>
    </div>
  );
}

export default function SciencePage() {
  const { t } = useI18n();
  return (
    <div>
      <PageHeader title={t("sci.title")} subtitle={t("sci.subtitle")} icon={<FlaskConical className="h-5 w-5" />} />
      <Guide id="science" />
      <Tabs tabs={[{ id: "exp", label: t("sci.experiments"), content: <Experiments /> }, { id: "models", label: t("sci.models"), content: <Models /> }, { id: "ds", label: t("sci.datasets"), content: <Datasets /> }]} />
    </div>
  );
}
