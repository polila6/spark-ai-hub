"use client";
import { Bot, Download, ExternalLink, Library, Play, Power, RefreshCw, Square, Terminal } from "lucide-react";
import { useState } from "react";
import useSWR from "swr";
import { Guide } from "@/components/guide";
import { Badge, Button, Card, CardBody, CardHeader, CopyButton, Field, Input, Notice, PageHeader, Skeleton, Textarea } from "@/components/ui";
import { api } from "@/lib/api";
import { bytes } from "@/lib/format";
import { useL } from "@/lib/i18n";
import { isManager, useMe, useToast } from "@/lib/providers";

function CopyField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="mb-1 text-xs text-subtle">{label}</div>
      <div className="flex items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-lg border border-border bg-bg px-3 py-2 text-sm">{value}</code>
        <CopyButton text={value} />
      </div>
    </div>
  );
}

function LlamaCard({ data, manager, act }: { data: any; manager: boolean; act: (path: string, json?: any) => Promise<void> }) {
  const L = useL();
  const server = data.server;
  if (server?.llama_installed) return null;
  const access = data.llama_access?.state;
  const repos: string[] = data.encoder_repos ?? [];
  const running = repos.some((r) => server?.downloads?.[r]?.state === "running");
  const failed = repos.map((r) => server?.downloads?.[r]).find((d: any) => d?.state === "failed");
  return (
    <Card className="mt-4">
      <CardHeader title={L("Falta un paso: instalar Llama 3", "One step left: install Llama 3")}
        subtitle={L("Kimodo convierte el texto en vectores con este modelo concreto, y los vectores de otro modelo (como el principal del hub) no le sirven. Ocupa unos 16 GB de disco y, cargado, unos 17 GB de memoria.",
          "Kimodo turns text into vectors with this exact model, and vectors from another model (such as the hub's main one) do not work. It takes about 16 GB of disk and, once loaded, about 17 GB of memory.")} />
      <CardBody className="space-y-3 text-sm">
        <ol className="list-decimal space-y-2 pl-5">
          <li>{L("Entra en Hugging Face con tu cuenta y pide acceso. Meta lo revisa, y suele tardar poco:", "Sign in to Hugging Face with your account and request access. Meta reviews it, usually quickly:")}{" "}
            <a className="text-accent underline" href="https://huggingface.co/meta-llama/Meta-Llama-3-8B-Instruct" target="_blank" rel="noreferrer">Meta-Llama-3-8B-Instruct <ExternalLink className="inline h-3 w-3" /></a></li>
          <li>{L("Crea un token de lectura en", "Create a read token at")}{" "}
            <a className="text-accent underline" href="https://huggingface.co/settings/tokens/new?tokenType=read" target="_blank" rel="noreferrer">huggingface.co/settings/tokens <ExternalLink className="inline h-3 w-3" /></a>{" "}
            {L("y pégalo en", "and paste it in")} <a className="text-accent underline" href="/settings">{L("Ajustes → Hugging Face", "Settings → Hugging Face")}</a>.</li>
          <li>{L("Vuelve aquí y pulsa «Instalar Llama 3».", "Come back and press “Install Llama 3”.")}</li>
        </ol>
        {access === "no_token" && <Notice tone="warn">{L("El hub aún no tiene ningún token de Hugging Face.", "The hub has no Hugging Face token yet.")}</Notice>}
        {access === "denied" && <Notice tone="warn">{L("El token del hub aún no tiene acceso a Llama 3: falta que Meta lo apruebe o el token es de otra cuenta.", "The hub's token has no access to Llama 3 yet: Meta has not approved it, or the token belongs to another account.")}</Notice>}
        {access === "granted" && <Notice tone="info">{L("El token del hub ya tiene acceso. Puedes instalarlo.", "The hub's token has access. You can install it.")}</Notice>}
        {failed && <Notice tone="warn">{L("La descarga falló:", "The download failed:")} {String(failed.error ?? "").slice(-200)}</Notice>}
        {manager && <Button disabled={access !== "granted" || running || !server} onClick={async () => { for (const repo of repos) await act("/install", { repo_id: repo }); }}>
          {running ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />} {running ? L("Descargando…", "Downloading…") : L("Instalar Llama 3", "Install Llama 3")}
        </Button>}
      </CardBody>
    </Card>
  );
}

function TestCard({ data }: { data: any }) {
  const L = useL();
  const { push } = useToast();
  const [prompt, setPrompt] = useState("a person waving hand");
  const [duration, setDuration] = useState(5);
  const [steps, setSteps] = useState(100);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const run = async () => {
    setBusy(true);
    setResult(null);
    try {
      setResult(await api("/api/v1/robot/test", { method: "POST", json: { prompt, duration, diffusion_steps: steps } }));
    } catch (e: any) {
      push("error", e?.code === "text_encoder_missing" ? L("Falta instalar Llama 3: sigue los pasos de esta página.", "Llama 3 is not installed yet: follow the steps on this page.") : e?.message ?? String(e));
    } finally {
      setBusy(false);
    }
  };
  const save = () => {
    const url = URL.createObjectURL(new Blob([result.csv], { type: "text/csv" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "movimiento.csv";
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <Card className="mt-4">
      <CardHeader title={L("Probar", "Try it")} subtitle={L("Genera un movimiento desde aquí, sin abrir tu programa. La instrucción, en inglés.", "Generate a motion from here, without opening your program. Write the prompt in English.")} />
      <CardBody className="space-y-3 text-sm">
        <Textarea rows={2} value={prompt} onChange={(e) => setPrompt(e.target.value)} aria-label={L("Instrucción", "Prompt")} />
        <div className="flex flex-wrap items-end gap-3">
          <Field label={L("Duración (s)", "Duration (s)")}><Input className="w-24" type="number" min={1} max={60} value={duration} onChange={(e) => setDuration(Number(e.target.value))} /></Field>
          <Field label={L("Pasos de difusión", "Diffusion steps")}><Input className="w-24" type="number" min={1} max={1000} value={steps} onChange={(e) => setSteps(Number(e.target.value))} /></Field>
          <Button disabled={busy || !prompt.trim() || data.container?.state !== "running"} onClick={run}>
            {busy ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />} {busy ? L("Generando…", "Generating…") : L("Generar", "Generate")}
          </Button>
        </div>
        {result && (
          <div className="flex flex-wrap items-center gap-3">
            <span>{L(`Hecho en ${result.elapsed_s} s: ${result.num_frames} fotogramas a ${result.fps} fps.`, `Done in ${result.elapsed_s} s: ${result.num_frames} frames at ${result.fps} fps.`)}</span>
            {result.csv && <Button size="sm" variant="ghost" onClick={save}><Download className="h-4 w-4" /> CSV</Button>}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function LibraryCard({ data, manager, act }: { data: any; manager: boolean; act: (path: string, json?: any) => Promise<void> }) {
  const L = useL();
  const running = data.container?.state === "running";
  const compatible = (data.library ?? []).filter((m: any) => m.compatible);
  const others = (data.library ?? []).filter((m: any) => !m.compatible);
  const row = (m: any) => (
    <div key={m.repo} className="flex flex-wrap items-center gap-2 border-t border-border py-3 first:border-t-0">
      <div className="min-w-0 basis-full sm:flex-1 sm:basis-0">
        <a className="font-medium hover:underline" href={m.url} target="_blank" rel="noreferrer">{m.name}</a>
        {m.recommended && <Badge className="ml-2" tone="accent">{L("recomendado", "recommended")}</Badge>}
        <div className="text-xs text-subtle">{m.skeleton} · {m.data} · {L("licencia", "licence")} {m.licence}</div>
        {m.download?.state === "failed" && <div className="text-xs text-danger">{L("La descarga falló.", "The download failed.")}</div>}
      </div>
      {m.loaded && <Badge tone="ok">{L("en memoria", "in memory")}</Badge>}
      {!m.loaded && m.installed && <Badge>{L("instalado", "installed")}</Badge>}
      {m.compatible && manager && running && (
        m.download?.state === "running" ? <span className="text-xs text-subtle">{L("Descargando…", "Downloading…")} {bytes(m.download.bytes ?? 0)}</span>
        : !m.installed ? <Button size="sm" onClick={() => act("/install", { repo_id: m.repo })}><Download className="h-4 w-4" /> {L("Instalar", "Install")}</Button>
        : m.loaded ? <Button size="sm" variant="ghost" onClick={() => act("/unload", { model: m.name })}>{L("Sacar de memoria", "Unload")}</Button>
        : <Button size="sm" onClick={() => act("/load", { model: m.name })}>{L("Cargar", "Load")}</Button>
      )}
    </div>
  );
  return (
    <Card className="mt-4">
      <CardHeader icon={<Library className="h-4 w-4" />} title={L("Biblioteca", "Library")}
        subtitle={L("Modelos de movimiento que este servidor puede desplegar para tu robot.", "Motion models this server can deploy for your robot.")} />
      <CardBody>
        {compatible.map(row)}
        {data.news?.length > 0 && (
          <Notice tone="info">{L("Hay modelos nuevos en Hugging Face que requieren actualizar Kimodo en el Spark:", "New models on Hugging Face need Kimodo to be updated on the Spark:")} {data.news.map((m: any) => m.name).join(", ")}</Notice>
        )}
        {others.length > 0 && (
          <details className="mt-3 text-sm">
            <summary className="cursor-pointer text-subtle">{L(`No compatibles con el Unitree G1 (${others.length})`, `Not compatible with the Unitree G1 (${others.length})`)}</summary>
            <p className="mt-2 text-xs text-subtle">{L("Generan movimiento para un esqueleto humano, no para el robot: tu programa no podría reproducirlos.", "They generate motion for a human skeleton, not the robot: your program could not play them.")}</p>
            {others.map(row)}
          </details>
        )}
      </CardBody>
    </Card>
  );
}

function RequestsCard() {
  const L = useL();
  const [open, setOpen] = useState(false);
  const { data } = useSWR(open ? "/api/v1/robot/requests" : null, { refreshInterval: 5000 });
  return (
    <Card className="mt-4">
      <CardHeader icon={<Terminal className="h-4 w-4" />} title={L("Últimas peticiones", "Latest requests")}
        subtitle={L("Lo que envían los programas conectados y lo que contesta el servidor. Sirve para diagnosticar errores de conexión.", "What connected programs send and what the server answers. Useful to diagnose connection errors.")}
        actions={<Button size="sm" variant="ghost" onClick={() => setOpen(!open)}>{open ? L("Ocultar", "Hide") : L("Ver", "Show")}</Button>} />
      {open && (
        <CardBody className="space-y-2 text-xs">
          {!data?.requests?.length && <p className="text-subtle">{L("Aún no ha llegado ninguna petición.", "No requests yet.")}</p>}
          {data?.requests?.map((r: any, i: number) => (
            <details key={i} className="rounded-lg border border-border p-2">
              <summary className="cursor-pointer">
                <Badge tone={r.status < 400 ? "ok" : "warn"}>{r.status}</Badge> <code>{r.method} {r.path}</code> <span className="text-subtle">{r.client} · {r.ms} ms · {new Date(r.at * 1000).toLocaleTimeString()}</span>
              </summary>
              <pre className="mt-2 overflow-x-auto whitespace-pre-wrap break-words">{JSON.stringify(r.body, null, 1)}</pre>
            </details>
          ))}
        </CardBody>
      )}
    </Card>
  );
}

export default function RobotPage() {
  const L = useL();
  const { me } = useMe();
  const { push } = useToast();
  const manager = isManager(me);
  const { data, mutate } = useSWR("/api/v1/robot", { refreshInterval: 4000 });
  if (!data) return <Skeleton className="h-64" />;
  const act = async (path: string, json?: any) => {
    try {
      await api(`/api/v1/robot${path}`, { method: "POST", json: json ?? {} });
    } catch (e: any) {
      push("error", e?.message ?? String(e));
    }
    mutate();
  };
  const running = data.container?.state === "running";
  const server = data.server;
  const urls = data.urls ?? {};
  const free = server?.available_gb;
  return (
    <div>
      <PageHeader title={L("Robot", "Robot")} subtitle={L("El Spark como cerebro de tu programa de control del robot", "The Spark as the brain of your robot control program")} icon={<Bot className="h-5 w-5" />} />
      <Guide id="robot" />
      <div className="grid gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader title={L("Conecta tu programa", "Connect your program")} subtitle={L("Pega esta dirección en la URL del servidor Kimodo. Solo funciona desde equipos conectados a Tailscale.", "Paste this address as the Kimodo server URL. It only works from computers connected to Tailscale.")} />
          <CardBody className="space-y-3">
            {urls.generate ? <CopyField label={L("Dirección de generación", "Generation address")} value={urls.generate} /> : <Notice tone="warn">{L("Tailscale aún no ha informado de la dirección de esta máquina.", "Tailscale has not reported this machine's address yet.")}</Notice>}
            {urls.ip && <CopyField label={L("Solo la IP", "Just the IP")} value={urls.ip} />}
            {urls.generate_dns && <CopyField label={L("Con nombre, por si cambia la IP", "By name, in case the IP changes")} value={urls.generate_dns} />}
          </CardBody>
        </Card>
        <Card>
          <CardHeader title={L("Estado", "Status")} actions={manager && (running
            ? <Button size="sm" variant="ghost" onClick={() => act("/stop")}><Square className="h-4 w-4" /> {L("Parar", "Stop")}</Button>
            : <Button size="sm" disabled={data.container?.image_present === false} onClick={() => act("/start")}><Power className="h-4 w-4" /> {L("Iniciar", "Start")}</Button>)} />
          <CardBody className="space-y-2 text-sm">
            <p><Badge tone={running ? "ok" : "warn"}>{running ? L("en marcha", "running") : L("parado", "stopped")}</Badge></p>
            {data.container?.image_present === false && <Notice tone="warn">{L("Falta construir la imagen: scripts/sah.sh robot-build", "The image must be built: scripts/sah.sh robot-build")}</Notice>}
            {data.error && running && <p className="text-subtle">{L("Arrancando…", "Starting…")}</p>}
            {server && <>
              <p>{L("Modelos en memoria:", "Models in memory:")} <b>{server.loaded?.map((m: any) => m.display).join(", ") || L("ninguno", "none")}</b>{server.loading?.length > 0 && ` · ${L("cargando", "loading")} ${server.loading.join(", ")}`}</p>
              <p>{L("Texto (Llama 3):", "Text (Llama 3):")} <b>{server.encoder_loaded ? L("en memoria", "in memory") : server.llama_installed ? L("instalado, se carga al usarlo", "installed, loads when used") : L("sin instalar", "not installed")}</b>
                {" · "}{L(`${server.cached_prompts} textos ya guardados`, `${server.cached_prompts} prompts saved`)}</p>
              <p>{L("Memoria libre en el Spark:", "Free memory on the Spark:")} <b>{free} GB</b>{!server.encoder_loaded && free < server.encoder_gb + server.margin_gb && ` · ${L(`Llama 3 necesita ${server.encoder_gb + server.margin_gb} GB para cargarse`, `Llama 3 needs ${server.encoder_gb + server.margin_gb} GB to load`)}`}</p>
              <p>{L("Física:", "Physics:")} <b>MuJoCo</b> · {L(`${server.generated} movimientos generados desde el último arranque`, `${server.generated} motions generated since the last start`)}</p>
              {server.last_error && <Notice tone="warn">{server.last_error}</Notice>}
            </>}
            {manager && running && server?.encoder_loaded && <Button size="sm" variant="ghost" onClick={() => act("/unload", { model: "text_encoder" })}>{L("Liberar Llama 3", "Release Llama 3")}</Button>}
          </CardBody>
        </Card>
      </div>
      <LlamaCard data={data} manager={manager} act={act} />
      <TestCard data={data} />
      <LibraryCard data={data} manager={manager} act={act} />
      {manager && <RequestsCard />}
    </div>
  );
}
