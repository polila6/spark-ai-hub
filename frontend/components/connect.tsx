"use client";
import { AlertTriangle, CheckCircle2, KeyRound } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useI18n, useL } from "@/lib/i18n";
import { Badge, Button, Code, ErrorBox, Field, Input, Notice, Select, Switch, cx } from "./ui";

export type OS = "windows" | "macos" | "linux";
type Step = { text: string; code?: string; lang?: string };
type ToolDef = { id: string; name: string; kind: string; desc: [string, string]; steps: (c: Ctx) => Step[] };
type Ctx = { base: string; key: string; hasKey: boolean; model: string; os: OS; models: any[]; main: string; extras: boolean; L: (es: string, en: string) => string };

const PLACEHOLDER = "sah-TU-CLAVE";

function detectOS(): OS {
  if (typeof navigator === "undefined") return "windows";
  const p = `${navigator.userAgent} ${(navigator as any).userAgentData?.platform ?? ""}`.toLowerCase();
  if (p.includes("win")) return "windows";
  if (p.includes("mac")) return "macos";
  return "linux";
}

function envStep(c: Ctx, name: string, value: string): Step {
  if (c.os === "windows")
    return { text: c.L(`Guarda la clave como variable de entorno (PowerShell; cierra y vuelve a abrir la terminal después):`, `Store the key as an environment variable (PowerShell; close and reopen the terminal afterwards):`), code: `setx ${name} "${value}"`, lang: "powershell" };
  const rc = c.os === "macos" ? "~/.zshrc" : "~/.bashrc";
  return { text: c.L(`Guarda la clave como variable de entorno (añádela a ${rc} y abre una terminal nueva):`, `Store the key as an environment variable (add it to ${rc} and open a new terminal):`), code: `echo 'export ${name}="${value}"' >> ${rc}`, lang: "bash" };
}

function cfgPath(c: Ctx, unix: string, win: string) {
  return c.os === "windows" ? win : unix;
}

function ctxFor(m: any) {
  return m?.context_window ?? (m?.backend === "ollama" ? 32768 : m?.context_length ?? 8192);
}

/** Whether a model is in memory right now (the main model is kept resident). */
function isLoaded(m: any) {
  return ["ready", "busy"].includes(m?.runtime?.state ?? "") || ["ready", "busy"].includes(m?.status);
}

/** OpenCode model entry with the model's real limits: output = min(32k, context/8). */
function openCodeModel(m: any, c: Ctx) {
  const ctx = ctxFor(m);
  const tag = m.alias === c.main ? c.L("principal", "main") : isLoaded(m) ? c.L("cargado", "loaded") : c.L("bajo demanda", "on demand");
  return { name: `${m.alias} (Spark · ${Math.round(ctx / 1024)}k · ${tag})`, limit: { context: ctx, output: Math.min(32768, Math.floor(ctx / 8)) },
    ...(m.reasoning ? { reasoning: true } : {}), ...(m.tools_enabled ? { tool_call: true } : {}) };
}

function openCodeConfig(c: Ctx) {
  const chosen = c.models.find((m) => m.alias === c.model);
  const list = c.extras ? [chosen, ...c.models.filter((m) => m.alias !== c.model)] : [chosen];
  const models = Object.fromEntries(list.filter(Boolean).map((m) => [m.alias, openCodeModel(m, c)]));
  // small_model = the same model: a second "light" model would have to be loaded too and slows the main one down.
  // headerTimeout: a model that is not in memory is loaded on the first request (vLLM takes ~5 min); OpenCode's default is 5 min
  return { $schema: "https://opencode.ai/config.json",
    provider: { spark: { npm: "@ai-sdk/openai-compatible", name: "Spark AI Hub", options: { baseURL: `${c.base}/v1`, apiKey: c.key, headerTimeout: 1800000 }, models } },
    model: `spark/${c.model}`, small_model: `spark/${c.model}`,
    mcp: { "spark-cloud": { type: "remote", url: `${c.base}/mcp`, headers: { Authorization: `Bearer ${c.key}` } } } };
}

const TOOLS: ToolDef[] = [
  {
    id: "opencode", name: "OpenCode", kind: "agent", desc: ["Agente de programación en la terminal.", "Terminal coding agent."],
    steps: (c) => {
      const win = c.os === "windows";
      const path = cfgPath(c, "~/.config/opencode/opencode.json", "%USERPROFILE%\\.config\\opencode\\opencode.json");
      const chosen = c.models.find((m) => m.alias === c.model);
      return [
        { text: c.L("Instala OpenCode (si no lo tienes):", "Install OpenCode (if you don't have it):"), code: win ? "npm install -g opencode-ai" : "curl -fsSL https://opencode.ai/install | bash", lang: win ? "powershell" : "bash" },
        { text: c.hasKey ? c.L("Tu clave de API ya va escrita dentro del archivo (en «apiKey» y en la cabecera del MCP): no hace falta ninguna variable de entorno.", "Your API key is already written inside the file (in “apiKey” and in the MCP header): no environment variable needed.")
          : c.L("Antes de copiar: pulsa «Crear clave para OpenCode» (arriba) o pega una clave tuya. Se escribirá sola dentro del archivo; mientras tanto verás «sah-TU-CLAVE».", "Before copying: press “Create key for OpenCode” (above) or paste a key of yours. It is written into the file automatically; until then you see “sah-TU-CLAVE”.") },
        { text: c.L(`Crea la carpeta y abre el archivo de configuración ${path} (o crea «opencode.json» en la carpeta de tu proyecto para que solo valga allí):`, `Create the folder and open the config file ${path} (or create “opencode.json” in your project folder so it only applies there):`),
          code: win ? `New-Item -ItemType Directory -Force "$env:USERPROFILE\\.config\\opencode" | Out-Null\nnotepad "$env:USERPROFILE\\.config\\opencode\\opencode.json"` : "mkdir -p ~/.config/opencode\nnano ~/.config/opencode/opencode.json", lang: win ? "powershell" : "bash" },
        { text: c.L("Borra lo que tenga y pega este contenido completo (botón Copiar). Guarda el archivo. Ojo: contiene tu clave, no lo compartas ni lo subas a git.", "Delete whatever it has and paste this whole content (Copy button). Save the file. Careful: it contains your key, do not share it or commit it to git."), code: JSON.stringify(openCodeConfig(c), null, 2), lang: "json" },
        { text: c.L("Reinicia OpenCode: ciérralo del todo (/exit o Ctrl+C) y vuelve a abrirlo en la carpeta de tu proyecto. Lee la configuración solo al arrancar.", "Restart OpenCode: close it completely (/exit or Ctrl+C) and open it again in your project folder. It only reads the configuration at start-up."), code: "opencode", lang: win ? "powershell" : "bash" },
        { text: c.L(`Escribe /models y elige «${c.model}» del proveedor «Spark AI Hub» (ya viene seleccionado por defecto). El modelo para las tareas internas (títulos, resúmenes) es el mismo, así no se carga ningún otro.`, `Type /models and pick “${c.model}” from the “Spark AI Hub” provider (it is already the default). Internal tasks (titles, summaries) use the same model, so no other model gets loaded.`) },
        { text: c.L("¿Quieres cambiar de modelo más adelante? Vuelve a esta página, elige otro en «¿Qué modelo quieres usar en OpenCode?», copia de nuevo el archivo entero y reinicia OpenCode.", "Want to change model later? Come back to this page, pick another one in “Which model do you want to use in OpenCode?”, copy the whole file again and restart OpenCode.") },
        ...(chosen && chosen.alias !== c.main ? [{ text: c.L(`Aviso: «${c.model}» no es el modelo principal. Se carga bajo demanda: la primera respuesta puede tardar de 1 a 6 minutos (OpenCode espera hasta 30 min gracias a «headerTimeout») y, si no cabe en memoria, puede descargar otros modelos. Para respuestas inmediatas usa el principal («${c.main}»).`, `Note: “${c.model}” is not the main model. It loads on demand: the first answer may take 1 to 6 minutes (OpenCode waits up to 30 min thanks to “headerTimeout”) and, if memory is short, other models may be unloaded. For instant answers use the main one (“${c.main}”).`) }] : []),
        { text: c.L(`Contexto: este modelo admite ${ctxFor(chosen)} tokens por petición (ya está en el archivo). Al acercarse al límite OpenCode resume la sesión solo; también puedes escribir /compact cuando quieras. Si se te queda corto, un administrador puede ampliarlo en Modelos → (el modelo) → «Ventana de contexto»; después vuelve a copiar este archivo.`, `Context: this model accepts ${ctxFor(chosen)} tokens per request (already in the file). Near the limit OpenCode summarises the session on its own; you can also type /compact. If it is too short, an administrator can raise it in Models → (the model) → “Context window”; then copy this file again.`) },
      ];
    },
  },
  {
    id: "claude-code", name: "Claude Code", kind: "agent", desc: ["Claude Code apuntando a los modelos locales (API compatible con Anthropic).", "Claude Code pointed at the local models (Anthropic-compatible API)."],
    steps: (c) => {
      const settings = { env: { ANTHROPIC_BASE_URL: c.base, ANTHROPIC_AUTH_TOKEN: c.key, ANTHROPIC_MODEL: c.model, ANTHROPIC_DEFAULT_SONNET_MODEL: c.model, ANTHROPIC_DEFAULT_OPUS_MODEL: c.model, ANTHROPIC_DEFAULT_HAIKU_MODEL: c.model, ANTHROPIC_SMALL_FAST_MODEL: c.model, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: "1" } };
      return [
        { text: c.L("Instala Claude Code (necesita Node.js 18+):", "Install Claude Code (needs Node.js 18+):"), code: "npm install -g @anthropic-ai/claude-code", lang: c.os === "windows" ? "powershell" : "bash" },
        { text: c.L(`Edita ${cfgPath(c, "~/.claude/settings.json", "%USERPROFILE%\\.claude\\settings.json")} y añade (o fusiona) este bloque "env":`, `Edit ${cfgPath(c, "~/.claude/settings.json", "%USERPROFILE%\\.claude\\settings.json")} and add (or merge) this "env" block:`), code: JSON.stringify(settings, null, 2), lang: "json" },
        { text: c.L("Opcional: conecta tu nube personal (documentos, archivos, chats, memoria) como servidor MCP:", "Optional: connect your personal cloud (documents, files, chats, memory) as an MCP server:"), code: `claude mcp add --transport http spark-cloud ${c.base}/mcp --header "Authorization: Bearer ${c.key}"`, lang: "bash" },
        { text: c.L("Abre Claude Code en tu proyecto. Con modelos locales, las tareas largas pueden ser más lentas que con Claude.", "Open Claude Code in your project. With local models, long tasks may be slower than with Claude."), code: "claude", lang: "bash" },
      ];
    },
  },
  {
    id: "continue", name: "Continue (VS Code / JetBrains)", kind: "editor", desc: ["Chat, autocompletado y edición en el editor.", "Chat, autocomplete and edits in the editor."],
    steps: (c) => [
      { text: c.L("Instala la extensión «Continue» desde el marketplace de VS Code o JetBrains.", "Install the “Continue” extension from the VS Code or JetBrains marketplace.") },
      { text: c.L(`Abre ${cfgPath(c, "~/.continue/config.yaml", "%USERPROFILE%\\.continue\\config.yaml")} (Continue → ⚙ → Open config) y pon:`, `Open ${cfgPath(c, "~/.continue/config.yaml", "%USERPROFILE%\\.continue\\config.yaml")} (Continue → ⚙ → Open config) and set:`), lang: "yaml",
        code: `name: Spark AI Hub\nversion: 1.0.0\nschema: v1\nmodels:\n  - name: ${c.model} (Spark)\n    provider: openai\n    model: ${c.model}\n    apiBase: ${c.base}/v1\n    apiKey: ${c.key}\n    roles: [chat, edit, apply, autocomplete]\nmcpServers:\n  - name: spark-cloud\n    type: streamable-http\n    url: ${c.base}/mcp\n    requestOptions:\n      headers:\n        Authorization: Bearer ${c.key}` },
      { text: c.L("Recarga VS Code y elige el modelo en el desplegable de Continue.", "Reload VS Code and pick the model in Continue's dropdown.") },
    ],
  },
  {
    id: "cline", name: "Cline / Roo Code", kind: "editor", desc: ["Agente autónomo dentro de VS Code.", "Autonomous agent inside VS Code."],
    steps: (c) => [
      { text: c.L("Instala la extensión «Cline» (o «Roo Code») en VS Code.", "Install the “Cline” (or “Roo Code”) extension in VS Code.") },
      { text: c.L("En sus ajustes elige API Provider = «OpenAI Compatible» y rellena:", "In its settings choose API Provider = “OpenAI Compatible” and fill in:"), code: `Base URL:  ${c.base}/v1\nAPI Key:   ${c.key}\nModel ID:  ${c.model}\nContext window: ${ctxFor(c.models.find((m) => m.alias === c.model))}`, lang: "text" },
      { text: c.L("En «MCP Servers» → «Remote servers» añade la URL de tu nube personal con la cabecera Authorization:", "In “MCP Servers” → “Remote servers” add your personal cloud URL with the Authorization header:"), code: `${c.base}/mcp\nAuthorization: Bearer ${c.key}`, lang: "text" },
    ],
  },
  {
    id: "cursor", name: "Cursor (MCP)", kind: "editor", desc: ["Tu nube personal como herramientas MCP en Cursor.", "Your personal cloud as MCP tools in Cursor."],
    steps: (c) => [
      { text: c.L(`Edita ${cfgPath(c, "~/.cursor/mcp.json", "%USERPROFILE%\\.cursor\\mcp.json")}:`, `Edit ${cfgPath(c, "~/.cursor/mcp.json", "%USERPROFILE%\\.cursor\\mcp.json")}:`), lang: "json",
        code: JSON.stringify({ mcpServers: { "spark-cloud": { url: `${c.base}/mcp`, headers: { Authorization: `Bearer ${c.key}` } } } }, null, 2) },
      { text: c.L("Para usar también los modelos del Spark en Cursor: Settings → Models → OpenAI API Key = tu clave y «Override OpenAI Base URL» = la URL /v1; añade el nombre del modelo.", "To also use the Spark models in Cursor: Settings → Models → OpenAI API Key = your key and “Override OpenAI Base URL” = the /v1 URL; add the model name."), code: `${c.base}/v1\n${c.model}`, lang: "text" },
    ],
  },
  {
    id: "claude-desktop", name: "Claude Desktop (MCP)", kind: "editor", desc: ["Acceso a tu nube personal desde Claude Desktop.", "Your personal cloud from Claude Desktop."],
    steps: (c) => [
      { text: c.L("Necesitas Node.js instalado. Abre Claude Desktop → Settings → Developer → Edit Config y añade:", "You need Node.js installed. Open Claude Desktop → Settings → Developer → Edit Config and add:"), lang: "json",
        code: JSON.stringify({ mcpServers: { "spark-cloud": { command: "npx", args: ["-y", "mcp-remote", `${c.base}/mcp`, "--header", "Authorization:${SPARK_AUTH}"], env: { SPARK_AUTH: `Bearer ${c.key}` } } } }, null, 2) },
      { text: c.L("Reinicia Claude Desktop; verás las herramientas «spark-cloud».", "Restart Claude Desktop; you'll see the “spark-cloud” tools.") },
    ],
  },
  {
    id: "aider", name: "Aider", kind: "agent", desc: ["Programación en pareja desde la terminal.", "Pair programming in the terminal."],
    steps: (c) => [
      { text: c.L("Instala Aider:", "Install Aider:"), code: "python -m pip install aider-install && aider-install", lang: "bash" },
      envStep(c, "OPENAI_API_KEY", c.key),
      { text: c.L("Ejecútalo en tu repositorio:", "Run it in your repository:"), code: `aider --model openai/${c.model} --openai-api-base ${c.base}/v1`, lang: "bash" },
    ],
  },
  {
    id: "python", name: "Python / Jupyter", kind: "code", desc: ["SDK oficial de OpenAI (o Anthropic) desde tus scripts y notebooks.", "Official OpenAI (or Anthropic) SDK from your scripts and notebooks."],
    steps: (c) => [
      { text: c.L("Instala el SDK:", "Install the SDK:"), code: "pip install openai", lang: "bash" },
      envStep(c, "SPARK_API_KEY", c.key),
      { text: c.L("Ejemplo (chat con streaming):", "Example (streaming chat):"), lang: "python",
        code: `import os\nfrom openai import OpenAI\n\nclient = OpenAI(base_url="${c.base}/v1", api_key=os.environ["SPARK_API_KEY"])\nfor chunk in client.chat.completions.create(\n        model="${c.model}", stream=True,\n        messages=[{"role": "user", "content": "Resume en 3 puntos qué es la memoria unificada"}]):\n    if chunk.choices:\n        print(chunk.choices[0].delta.content or "", end="")` },
      { text: c.L("Embeddings (para tu propio RAG):", "Embeddings (for your own RAG):"), lang: "python",
        code: `emb = client.embeddings.create(model="qwen3-embedding-0.6b", input=["texto uno", "texto dos"])\nprint(len(emb.data[0].embedding))` },
    ],
  },
  {
    id: "langchain", name: "LangChain / LlamaIndex", kind: "code", desc: ["Frameworks de agentes y RAG.", "Agent and RAG frameworks."],
    steps: (c) => [
      { text: "pip install langchain-openai", code: "pip install langchain-openai", lang: "bash" },
      { text: c.L("Uso:", "Usage:"), lang: "python", code: `import os\nfrom langchain_openai import ChatOpenAI, OpenAIEmbeddings\n\nllm = ChatOpenAI(model="${c.model}", base_url="${c.base}/v1", api_key=os.environ["SPARK_API_KEY"])\nemb = OpenAIEmbeddings(model="qwen3-embedding-0.6b", base_url="${c.base}/v1", api_key=os.environ["SPARK_API_KEY"],\n                       check_embedding_ctx_length=False)\nprint(llm.invoke("Hola").content)` },
    ],
  },
  {
    id: "openwebui", name: "Open WebUI / LibreChat", kind: "editor", desc: ["Otras interfaces de chat que admiten APIs compatibles con OpenAI.", "Other chat UIs that accept OpenAI-compatible APIs."],
    steps: (c) => [{ text: c.L("Administración → Conexiones → OpenAI API → añade:", "Admin → Connections → OpenAI API → add:"), code: `URL: ${c.base}/v1\nKey: ${c.key}`, lang: "text" }],
  },
  {
    id: "curl", name: "curl", kind: "code", desc: ["Prueba rápida desde la terminal.", "Quick test from the terminal."],
    steps: (c) => [
      { text: c.L("Chat (OpenAI):", "Chat (OpenAI):"), lang: c.os === "windows" ? "powershell" : "bash",
        code: c.os === "windows"
          ? `curl.exe ${c.base}/v1/chat/completions -H "Authorization: Bearer ${c.key}" -H "Content-Type: application/json" -d '{\\"model\\":\\"${c.model}\\",\\"messages\\":[{\\"role\\":\\"user\\",\\"content\\":\\"Hola\\"}]}'`
          : `curl ${c.base}/v1/chat/completions \\\n  -H "Authorization: Bearer ${c.key}" -H "Content-Type: application/json" \\\n  -d '{"model":"${c.model}","messages":[{"role":"user","content":"Hola"}]}'` },
      { text: c.L("Lista de modelos:", "Model list:"), code: `curl${c.os === "windows" ? ".exe" : ""} ${c.base}/v1/models -H "Authorization: Bearer ${c.key}"`, lang: "bash" },
    ],
  },
];

function checkStep(c: Ctx): Step {
  return {
    text: c.L("Comprobación final: si ves una lista de modelos en formato JSON, la conexión y la clave funcionan.", "Final check: if you see a JSON list of models, the connection and the key work."),
    code: `curl${c.os === "windows" ? ".exe" : ""} -s ${c.base}/v1/models -H "Authorization: Bearer ${c.key}"`,
    lang: c.os === "windows" ? "powershell" : "bash",
  };
}

const LIVE = ["installed", "ready", "busy", "loading"];

/** "¿Qué modelo quieres usar en OpenCode?": chat LLMs with real context, tools and whether they are in memory. */
function ModelPicker({ c, onPick, extras, onExtras }: { c: Ctx; onPick: (alias: string) => void; extras: boolean; onExtras: (v: boolean) => void }) {
  const L = c.L;
  return (
    <div className="space-y-2 rounded-xl border border-border p-3">
      <p className="text-sm font-semibold">{L("¿Qué modelo quieres usar en OpenCode?", "Which model do you want to use in OpenCode?")}</p>
      <div className="space-y-1.5" role="radiogroup">
        {c.models.map((m: any) => {
          const main = m.alias === c.main;
          const on = m.alias === c.model;
          return (
            <label key={m.id} className={cx("flex cursor-pointer items-start gap-2.5 rounded-lg border px-3 py-2 text-sm", on ? "border-accent bg-accent/5" : "border-border hover:border-accent/50")}>
              <input type="radio" name="opencode-model" className="mt-1 accent-accent" checked={on} onChange={() => onPick(m.alias)} />
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="font-medium">{m.alias}</span>
                  <Badge>{Math.round(ctxFor(m) / 1024)}k {L("de contexto", "context")}</Badge>
                  {m.tools_enabled ? <Badge tone="info">tools</Badge> : <Badge tone="warn">{L("sin herramientas", "no tools")}</Badge>}
                  {m.reasoning && <Badge>{L("razona", "reasoning")}</Badge>}
                  {m.capabilities?.includes("vision") && <Badge>{L("visión", "vision")}</Badge>}
                </div>
                <p className={cx("mt-0.5 text-[11px]", main ? "text-ok" : isLoaded(m) ? "text-info" : "text-warn")}>
                  {main ? L("Principal, siempre cargado: responde al instante. Recomendado.", "Main, always loaded: answers instantly. Recommended.")
                    : isLoaded(m) ? L("Cargado ahora, pero no es el principal: puede descargarse cuando no se use.", "Loaded now, but not the main one: it may be unloaded when idle.")
                    : L("Se carga bajo demanda: la primera respuesta tarda y puede descargar otros modelos.", "Loads on demand: the first answer takes a while and may unload other models.")}
                  {!m.tools_enabled && ` ${L("OpenCode no podrá leer ni editar archivos con este modelo.", "OpenCode cannot read or edit files with this model.")}`}
                </p>
              </div>
            </label>
          );
        })}
      </div>
      <Switch checked={extras} onChange={onExtras} label={L("Añadir también los demás modelos como alternativas en /models", "Also add the other models as alternatives in /models")} />
      {extras && <Notice tone="warn" icon={<AlertTriangle className="h-4 w-4" />}>{L("Los modelos que no son el principal se cargan bajo demanda: al elegirlos en /models la primera respuesta tarda minutos y, mientras están cargados, el principal va más lento para todos (o se descargan otros). Úsalos solo si los necesitas.", "Models other than the main one load on demand: when you pick them in /models the first answer takes minutes and, while they are loaded, the main model is slower for everyone (or others get unloaded). Use them only if you need them.")}</Notice>}
      <p className="text-[11px] text-subtle">{L("OpenCode usará este mismo modelo también para sus tareas internas («small_model»): cargar un segundo modelo «ligero» ralentiza mucho el principal.", "OpenCode also uses this same model for its internal tasks (“small_model”): loading a second “light” model slows the main one down a lot.")}</p>
    </div>
  );
}

/** Step-by-step setup for a tool + OS, with the user's real URL, models and (optionally) a just-created key. */
export function ConnectSteps({ initialKey, initialTool = "opencode", compact }: { initialKey?: string | null; initialTool?: string; compact?: boolean }) {
  const { t } = useI18n();
  const L = useL();
  const { data: models } = useSWR("/api/v1/models");
  const { data: pub } = useSWR("/api/v1/settings/public");
  const { data: mainInfo } = useSWR("/api/v1/models/main");
  const mainAlias: string = mainInfo?.alias || pub?.chat_default_model || "";
  const chat = useMemo(() => (models ?? [])
    .filter((m: any) => m.capabilities?.includes("chat") && m.modality !== "embedding" && LIVE.includes(m.status))
    .sort((a: any, b: any) => Number(b.alias === mainAlias) - Number(a.alias === mainAlias) || Number(isLoaded(b)) - Number(isLoaded(a))
      || Number(!!b.tools_enabled) - Number(!!a.tools_enabled) || (b.parameters_b ?? 0) - (a.parameters_b ?? 0)), [models, mainAlias]);
  const [tool, setTool] = useState(initialTool);
  const [os, setOS] = useState<OS>("windows");
  // The key lives only in this component's memory (never in localStorage): it is shown once and written into the snippets.
  const [key, setKey] = useState<string>(initialKey || "");
  const [created, setCreated] = useState<boolean>(!!initialKey);
  const [model, setModel] = useState("");
  const [extras, setExtras] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<any>(null);
  const [base, setBase] = useState("");
  useEffect(() => { setOS(detectOS()); setBase(window.location.origin); }, []);
  useEffect(() => { if (initialKey) { setKey(initialKey); setCreated(true); } }, [initialKey]);
  useEffect(() => {
    if (!chat.length) return;
    const withTools = chat.filter((m: any) => m.tools_enabled);
    const def = (chat.some((m: any) => m.alias === mainAlias) && mainAlias) || pub?.harness_default_model || withTools[0]?.alias || chat[0].alias;
    setModel((m) => m || def);
  }, [chat, pub, mainAlias]);
  const def = TOOLS.find((x) => x.id === tool) ?? TOOLS[0];
  const hasKey = key.trim().length > 8;
  const ctx: Ctx = { base: base || (typeof window !== "undefined" ? window.location.origin : ""), key: hasKey ? key.trim() : PLACEHOLDER, hasKey,
    model: model || chat[0]?.alias || "", os, models: chat, main: mainAlias, extras, L };
  const steps = [...def.steps(ctx), checkStep(ctx)];
  async function createKey() {
    setBusy(true);
    setErr(null);
    try {
      const day = new Date().toISOString().slice(0, 10);
      const r = await api("/api/v1/api-keys", { method: "POST", json: { name: `${def.name.split(" ")[0]} · ${day}`.slice(0, 120), expires_in_days: 365, scopes: ["inference"] } });
      setKey(r.key);
      setCreated(true);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-4">
      <div className={cx("grid gap-3", compact ? "sm:grid-cols-2" : "sm:grid-cols-3")}>
        <Field label={L("Herramienta", "Tool")}><Select value={tool} onChange={(e) => setTool(e.target.value)}>{TOOLS.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</Select></Field>
        <Field label={L("Sistema operativo", "Operating system")}><Select value={os} onChange={(e) => setOS(e.target.value as OS)}><option value="windows">Windows</option><option value="macos">macOS</option><option value="linux">Linux</option></Select></Field>
        {tool !== "opencode" && <Field label={L("Modelo", "Model")} hint={mainAlias ? L(`Recomendado: ${mainAlias} (principal, siempre cargado)`, `Recommended: ${mainAlias} (main, always loaded)`) : undefined}>
          <Select value={model} onChange={(e) => setModel(e.target.value)}>{chat.map((m: any) => <option key={m.id} value={m.alias}>{m.alias}{m.alias === mainAlias ? L(" · principal", " · main") : isLoaded(m) ? "" : L(" · bajo demanda", " · on demand")}{m.tools_enabled ? " · tools" : ""}{m.capabilities.includes("vision") ? " · vision" : ""}</option>)}</Select></Field>}
      </div>
      <p className="text-xs text-muted">{L(def.desc[0], def.desc[1])}</p>
      {tool === "opencode" && chat.length > 0 && <ModelPicker c={ctx} onPick={setModel} extras={extras} onExtras={setExtras} />}
      {created && hasKey ? (
        <Notice tone="ok" icon={<CheckCircle2 className="h-4 w-4" />}>
          {L("Clave creada y ya escrita en todos los pasos. Cópiala ahora o copia la configuración: no se volverá a mostrar.", "Key created and already written into every step. Copy it now or copy the configuration: it will not be shown again.")}
          <code className="ml-1 break-all font-mono">{key}</code>
          <p className="mt-1 text-warn">{L("Los archivos de configuración llevan ahora un secreto: no los compartas ni los subas a git. Si se filtra, revócala en Centro API.", "The configuration files now contain a secret: do not share them or commit them to git. If it leaks, revoke it in API centre.")}</p>
        </Notice>
      ) : (
        <Notice tone={hasKey ? "info" : "warn"} icon={<KeyRound className="h-4 w-4" />}>
          <div className="flex flex-wrap items-center gap-3">
            <span>{hasKey ? L("Usando la clave que has pegado (escrita dentro de la configuración).", "Using the key you pasted (written inside the configuration).")
              : L(`Los ejemplos usan «${PLACEHOLDER}». Crea una clave y se escribirá sola dentro de la configuración, o pega una que ya tengas:`, `Examples use “${PLACEHOLDER}”. Create a key and it is written into the configuration, or paste one you have:`)}</span>
            {!hasKey && <Button size="sm" variant="primary" loading={busy} onClick={createKey}><KeyRound className="h-3.5 w-3.5" />{tool === "opencode" ? L("Crear clave para OpenCode", "Create key for OpenCode") : L("Crear clave", "Create key")}</Button>}
            <Input className="h-8 max-w-[260px]" type="password" autoComplete="off" placeholder="sah-…" value={key} onChange={(e) => setKey(e.target.value.trim())} aria-label="API key" />
          </div>
          {hasKey && <p className="mt-1 text-warn">{L("La configuración lleva tu clave: no la compartas ni la subas a git.", "The configuration contains your key: do not share it or commit it to git.")}</p>}
        </Notice>
      )}
      {err && <ErrorBox error={err} />}
      <ol className="space-y-4">
        {steps.map((s, i) => (
          <li key={`${tool}-${i}`} className="flex gap-3">
            <span className="mt-0.5 grid h-6 w-6 shrink-0 place-items-center rounded-full bg-accent/15 text-xs font-semibold text-accent">{i + 1}</span>
            <div className="min-w-0 flex-1 space-y-2">
              <p className="text-sm text-fg">{s.text}</p>
              {s.code && <Code>{s.code}</Code>}
            </div>
          </li>
        ))}
      </ol>
      <div className="flex flex-wrap gap-2 text-[11px] text-muted">
        <Badge>{L("OpenAI", "OpenAI")}: {ctx.base}/v1</Badge><Badge>Anthropic: {ctx.base}/v1/messages</Badge><Badge>MCP: {ctx.base}/mcp</Badge>
        <span>{t("api.key_once")}</span>
      </div>
    </div>
  );
}

export const CONNECT_TOOLS = TOOLS.map((x) => ({ id: x.id, name: x.name, kind: x.kind }));
