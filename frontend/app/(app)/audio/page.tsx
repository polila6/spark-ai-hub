"use client";
import { AudioLines, Download, FileAudio, Mic } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import useSWR from "swr";
import { Guide } from "@/components/guide";
import { Badge, Button, Card, CardBody, CardHeader, Empty, ErrorBox, Field, Input, Notice, PageHeader, Progress, Select, StatusBadge, Switch, Textarea } from "@/components/ui";
import { api, upload } from "@/lib/api";
import { dt, num } from "@/lib/format";
import { useI18n } from "@/lib/i18n";
import { useToast } from "@/lib/providers";

function JobResult({ id }: { id: string }) {
  const { t } = useI18n();
  const { data: j } = useSWR(`/api/v1/jobs/${id}`, { refreshInterval: (d: any) => (d && ["completed", "failed", "cancelled"].includes(d.status) ? 0 : 1500) });
  if (!j) return null;
  return (
    <Card className="p-4">
      <div className="flex items-center gap-2"><StatusBadge state={j.status} /><span className="text-sm">{j.title}</span></div>
      {!["completed", "failed", "cancelled"].includes(j.status) && <div className="mt-2"><Progress value={j.progress * 100} /><p className="mt-1 text-[11px] text-muted">{j.message}</p></div>}
      {j.error && <p className="mt-2 text-xs text-danger">{j.error}</p>}
      {j.result?.text && <div className="mt-3 max-h-72 overflow-y-auto whitespace-pre-wrap rounded-lg bg-elevated p-3 text-sm">{j.result.text}</div>}
      {j.result?.meta && <p className="mt-2 text-[11px] text-muted">{j.result.meta.language && `${t("aud.language")}: ${j.result.meta.language} · `}{j.result.meta.duration_s && `${num(j.result.meta.duration_s)} s audio · `}{j.result.meta.processing_s && `${num(j.result.meta.processing_s)} s ${t("common.measured")}`} · {j.result.meta.model}</p>}
      <div className="mt-2 flex flex-wrap gap-2">
        {j.artifacts?.map((a: any) => a.mime.startsWith("audio") ? <audio key={a.id} controls src={a.url} className="w-full" /> : (
          <a key={a.id} href={`${a.url}?download=true`}><Button size="sm" variant="outline"><Download className="h-3.5 w-3.5" />{a.filename}</Button></a>
        ))}
      </div>
    </Card>
  );
}

export default function AudioPage() {
  const { t } = useI18n();
  const { push } = useToast();
  const { data: models } = useSWR("/api/v1/models?backend=mlworker");
  const { data: engines } = useSWR("/api/v1/engines");
  const asr = (models ?? []).filter((m: any) => m.task === "automatic-speech-recognition" && ["installed", "ready"].includes(m.status));
  const tts = (models ?? []).filter((m: any) => ["text-to-speech", "text-to-audio"].includes(m.task) && ["installed", "ready"].includes(m.status));
  const [file, setFile] = useState<any>(null);
  const [asrModel, setAsrModel] = useState("");
  const [lang, setLang] = useState("");
  const [translate, setTranslate] = useState(false);
  const [ttsModel, setTtsModel] = useState("");
  const [text, setText] = useState("");
  const [rate, setRate] = useState(1);
  // Recent jobs come from the server, so leaving the page (or another window) never loses one in progress.
  const { data: asrJobs, mutate: mutAsr } = useSWR("/api/v1/jobs?type=audio_transcribe&limit=5", { refreshInterval: 5000 });
  const { data: ttsJobs, mutate: mutTts } = useSWR("/api/v1/jobs?type=audio_tts&limit=5", { refreshInterval: 5000 });
  const asList = (d: any) => (Array.isArray(d) ? d : d?.items ?? d?.jobs ?? []);
  const jobs: string[] = [...asList(asrJobs), ...asList(ttsJobs)].sort((a: any, b: any) => String(b.created_at).localeCompare(String(a.created_at))).slice(0, 8).map((j: any) => j.id);
  const setJobs = (_: any) => { mutAsr(); mutTts(); };
  const [err, setErr] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const mlw = engines?.mlworker;
  return (
    <div>
      <PageHeader title={t("aud.title")} subtitle={t("aud.subtitle")} icon={<AudioLines className="h-5 w-5" />} />
      <Guide id="audio" />
      {mlw && mlw.status === "not_installed" && <div className="mb-4"><Notice tone="warn">ML worker no instalado: {mlw.detail}</Notice></div>}
      <Notice tone="muted">Diarización de hablantes: no disponible (requiere modelos gated pyannote; no instalados). Solo se ofrecen modalidades con un pipeline verificado.</Notice>
      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title={t("aud.transcribe")} subtitle="Whisper (transformers) · ASR, detección de idioma, traducción al inglés, SRT" icon={<Mic className="h-4 w-4" />} />
          <CardBody className="space-y-3">
            {asr.length === 0 ? <Empty title="No hay modelos ASR instalados" hint="Instala p. ej. openai/whisper-large-v3-turbo desde Descubrir modelos (motor mlworker)" action={<Link href="/discover"><Button size="sm">{t("nav.discover")}</Button></Link>} /> : (
              <>
                <Field label={t("common.model")}><Select value={asrModel} onChange={(e) => setAsrModel(e.target.value)}>{asr.map((m: any) => <option key={m.id} value={m.id}>{m.alias}</option>)}</Select></Field>
                <label className="flex cursor-pointer items-center gap-2 rounded-xl border border-dashed border-border px-4 py-5 text-sm text-muted hover:border-accent">
                  <FileAudio className="h-4 w-4" />{file ? `${file.filename} · ${(file.size_bytes / 1e6).toFixed(1)} MB` : `${t("common.upload")} (wav, mp3, flac, ogg, m4a)`}
                  <input type="file" accept="audio/*" className="sr-only" onChange={async (e) => {
                    const f = e.target.files?.[0]; if (!f) return; setErr(null);
                    try { const fd = new FormData(); fd.append("file", f); setFile(await upload("/api/v1/media/uploads?kind=audio", fd)); } catch (e) { setErr(e); }
                  }} />
                </label>
                <div className="grid grid-cols-2 gap-3">
                  <Field label={t("aud.language")} hint="vacío = detección automática"><Input value={lang} placeholder="es, en, …" onChange={(e) => setLang(e.target.value)} /></Field>
                  <div className="flex items-end"><Switch checked={translate} onChange={setTranslate} label={t("aud.translate")} /></div>
                </div>
                <Button variant="primary" disabled={!file} loading={busy} onClick={async () => {
                  setBusy(true); setErr(null);
                  try { const r = await api("/api/v1/media/audio/transcribe", { method: "POST", json: { artifact_id: file.id, model_id: asrModel || asr[0].id, language: lang || null, task: translate ? "translate" : "transcribe" } }); setJobs(r.job_id); }
                  catch (e) { setErr(e); } finally { setBusy(false); }
                }}>{t("aud.transcribe")}</Button>
              </>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title={t("aud.tts")} subtitle="VITS / MMS-TTS (transformers)" icon={<AudioLines className="h-4 w-4" />} />
          <CardBody className="space-y-3">
            {tts.length === 0 ? <Empty title="No hay modelos TTS instalados" hint="Instala p. ej. facebook/mms-tts-spa (licencia CC-BY-NC-4.0) desde Descubrir modelos" /> : (
              <>
                <Field label={t("common.model")}><Select value={ttsModel} onChange={(e) => setTtsModel(e.target.value)}>{tts.map((m: any) => <option key={m.id} value={m.id}>{m.alias} {m.license && `· ${m.license}`}</option>)}</Select></Field>
                <Field label={t("aud.text")}><Textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} maxLength={5000} /></Field>
                <Field label={`speaking_rate: ${rate}`}><input type="range" min={0.5} max={2} step={0.05} value={rate} onChange={(e) => setRate(Number(e.target.value))} className="w-full" aria-label="speaking rate" /></Field>
                <Button variant="primary" disabled={!text} onClick={async () => {
                  setErr(null);
                  try { const r = await api("/api/v1/media/audio/tts", { method: "POST", json: { text, model_id: ttsModel || tts[0].id, speaking_rate: rate } }); setJobs(r.job_id); }
                  catch (e) { setErr(e); }
                }}>{t("aud.tts")}</Button>
              </>
            )}
          </CardBody>
        </Card>
      </div>
      {err && <div className="mt-4"><ErrorBox error={err} /></div>}
      <div className="mt-4 space-y-3">{jobs.map((j) => <JobResult key={j} id={j} />)}</div>
      <RecentAudio />
    </div>
  );
}

function RecentAudio() {
  const { t, lang } = useI18n();
  const { data } = useSWR("/api/v1/jobs?type=audio_transcribe&limit=5");
  const { data: d2 } = useSWR("/api/v1/jobs?type=audio_tts&limit=5");
  const items = [...(data?.items ?? []), ...(d2?.items ?? [])].sort((a, b) => b.created_at.localeCompare(a.created_at));
  if (!items.length) return null;
  return (
    <Card className="mt-4">
      <CardHeader title={t("media.history")} />
      <ul className="divide-y divide-border/60">{items.map((j: any) => <li key={j.id} className="flex items-center gap-3 px-4 py-2 text-sm"><StatusBadge state={j.status} /><span className="flex-1 truncate">{j.title}</span><Badge>{j.type}</Badge><span className="text-xs text-muted">{dt(j.created_at, lang)}</span><Link className="text-xs text-accent" href={`/jobs?open=${j.id}`}>→</Link></li>)}</ul>
    </Card>
  );
}
