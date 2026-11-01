"use client";
import { CheckCircle2, KeyRound } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import { api } from "@/lib/api";
import { useI18n, useL } from "@/lib/i18n";
import { Badge, Button, Code, ErrorBox, Field, Input, Notice, Select, cx } from "./ui";

export type OS = "windows" | "macos" | "linux";
type Step = { text: string; code?: string; lang?: string };
type ToolDef = { id: string; name: string; kind: string; desc: [string, string]; steps: (c: Ctx) => Step[] };
type Ctx = { base: string; key: string; model: string; small: string; os: OS; models: any[]; L: (es: string, en: string) => string };

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

const TOOLS: ToolDef[] = [
  {
    id: "opencode", name: "OpenCode", kind: "agent", desc: ["Agente de programación en la terminal.", "Terminal coding agent."],
    steps: (c) => {
      const models = Object.fromEntries(c.models.filter((m) => m.tools_enabled).map((m) => [m.alias, { name: `${m.alias} (Spark)`, limit: { context: ctxFor(m), output: Math.min(16384, Math.floor(ctxFor(m) / 4)) } }]));
      const cfg = { $schema: "https://opencode.ai/config.json", provider: { spark: { npm: "@ai-sdk/openai-compatible", name: "Spark AI Hub", options: { baseURL: `${c.base}/v1`, apiKey: "{env:SPARK_API_KEY}" }, models } },
        model: `spark/${c.model}`, small_model: `spark/${c.small}`,
        mcp: { "spark-cloud": { type: "remote", url: `${c.base}/mcp`, headers: { Authorization: "Bearer {env:SPARK_API_KEY}" } } } };
      return [
        { text: c.L("Instala OpenCode (si no lo tienes):", "Install OpenCode (if you don't have it):"), code: c.os === "windows" ? "npm install -g opencode-ai" : "curl -fsSL https://opencode.ai/install | bash", lang: c.os === "windows" ? "powershell" : "bash" },
        envStep(c, "SPARK_API_KEY", c.key),
        { text: c.L(`Crea o edita el archivo ${cfgPath(c, "~/.config/opencode/opencode.json", "%USERPROFILE%\\.config\\opencode\\opencode.json")} con este contenido (incluye el servidor MCP de tu nube personal):`, `Create or edit ${cfgPath(c, "~/.config/opencode/opencode.json", "%USERPROFILE%\\.config\\opencode\\opencode.json")} with this content (includes the MCP server of your personal cloud):`), code: JSON.stringify(cfg, null, 2), lang: "json" },
        { text: c.L("Abre OpenCode en la carpeta de tu proyecto y elige el modelo con /models:", "Open OpenCode in your project folder and pick the model with /models:"), code: "opencode", lang: "bash" },
        { text: c.L(`Contexto: este modelo admite ${ctxFor(c.models.find((m) => m.alias === c.model))} tokens por petición (ya está en el archivo de arriba). Al acercarse al límite OpenCode resume la sesión solo; también puedes escribir /compact cuando quieras. Si se te queda corto, un administrador puede ampliarlo en Modelos → (el modelo) → «Ventana de contexto»; después vuelve a copiar este archivo.`, `Context: this model accepts ${ctxFor(c.models.find((m) => m.alias === c.model))} tokens per request (already in the file above). Near the limit OpenCode summarises the session on its own; you can also type /compact. If it is too short, an administrator can raise it in Models → (the model) → “Context window”; then copy this file again.`) },
      ];
    },
  },
  {
    id: "claude-code", name: "Claude Code", kind: "agent", desc: ["Claude Code apuntando a los modelos locales (API compatible con Anthropic).", "Claude Code pointed at the local models (Anthropic-compatible API)."],
    steps: (c) => {
      const settings = { env: { ANTHROPIC_BASE_URL: c.base, ANTHROPIC_AUTH_TOKEN: c.key, ANTHROPIC_MODEL: c.model, ANTHROPIC_DEFAULT_SONNET_MODEL: c.model, ANTHROPIC_DEFAULT_OPUS_MODEL: c.model, ANTHROPIC_DEFAULT_HAIKU_MODEL: c.small, ANTHROPIC_SMALL_FAST_MODEL: c.small, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC: "1" } };
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
        code: `name: Spark AI Hub\nversion: 1.0.0\nschema: v1\nmodels:\n  - name: ${c.model} (Spark)\n    provider: openai\n    model: ${c.model}\n    apiBase: ${c.base}/v1\n    apiKey: ${c.key}\n    roles: [chat, edit, apply]\n  - name: ${c.small} (Spark, rápido)\n    provider: openai\n    model: ${c.small}\n    apiBase: ${c.base}/v1\n    apiKey: ${c.key}\n    roles: [autocomplete]\nmcpServers:\n  - name: spark-cloud\n    type: streamable-http\n    url: ${c.base}/mcp\n    requestOptions:\n      headers:\n        Authorization: Bearer ${c.key}` },
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

/** Step-by-step setup for a tool + OS, with the user's real URL, models and (optionally) a just-created key. */
export function ConnectSteps({ initialKey, initialTool = "opencode", compact }: { initialKey?: string | null; initialTool?: string; compact?: boolean }) {
  const { t } = useI18n();
  const L = useL();
  const { data: models } = useSWR("/api/v1/models");
  const { data: pub } = useSWR("/api/v1/settings/public");
  const chat = useMemo(() => (models ?? []).filter((m: any) => m.capabilities?.includes("chat") && ["installed", "ready"].includes(m.status)), [models]);
  const [tool, setTool] = useState(initialTool);
  const [os, setOS] = useState<OS>("windows");
  const [key, setKey] = useState<string>(initialKey || "");
  const [model, setModel] = useState("");
  const [small, setSmall] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<any>(null);
  const [base, setBase] = useState("");
  useEffect(() => { setOS(detectOS()); setBase(window.location.origin); }, []);
  useEffect(() => { if (initialKey) setKey(initialKey); }, [initialKey]);
  useEffect(() => {
    if (!chat.length) return;
    const withTools = chat.filter((m: any) => m.tools_enabled);
    const def = pub?.harness_default_model || withTools.sort((a: any, b: any) => (b.parameters_b ?? 0) - (a.parameters_b ?? 0))[0]?.alias || chat[0].alias;
    const sm = pub?.harness_small_model || withTools.sort((a: any, b: any) => (a.parameters_b ?? 99) - (b.parameters_b ?? 99))[0]?.alias || def;
    setModel((m) => m || def);
    setSmall((m) => m || sm);
  }, [chat, pub]);
  const def = TOOLS.find((x) => x.id === tool) ?? TOOLS[0];
  const ctx: Ctx = { base: base || (typeof window !== "undefined" ? window.location.origin : ""), key: key || PLACEHOLDER, model: model || "qwen3.5-35b-a3b", small: small || model || "qwen3-4b-instruct", os, models: chat, L };
  const steps = [...def.steps(ctx), checkStep(ctx)];
  async function createKey() {
    setBusy(true);
    setErr(null);
    try {
      const r = await api("/api/v1/api-keys", { method: "POST", json: { name: `${def.name} (${os})`.slice(0, 120), expires_in_days: 365, scopes: ["inference"] } });
      setKey(r.key);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-4">
      <div className={cx("grid gap-3", compact ? "sm:grid-cols-2" : "sm:grid-cols-4")}>
        <Field label={L("Herramienta", "Tool")}><Select value={tool} onChange={(e) => setTool(e.target.value)}>{TOOLS.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</Select></Field>
        <Field label={L("Sistema operativo", "Operating system")}><Select value={os} onChange={(e) => setOS(e.target.value as OS)}><option value="windows">Windows</option><option value="macos">macOS</option><option value="linux">Linux</option></Select></Field>
        <Field label={L("Modelo principal", "Main model")}><Select value={model} onChange={(e) => setModel(e.target.value)}>{chat.map((m: any) => <option key={m.id} value={m.alias}>{m.alias}{m.tools_enabled ? " · tools" : ""}{m.capabilities.includes("vision") ? " · vision" : ""}</option>)}</Select></Field>
        <Field label={L("Modelo rápido", "Fast model")}><Select value={small} onChange={(e) => setSmall(e.target.value)}>{chat.map((m: any) => <option key={m.id} value={m.alias}>{m.alias}</option>)}</Select></Field>
      </div>
      <p className="text-xs text-muted">{L(def.desc[0], def.desc[1])}</p>
      {key && key !== PLACEHOLDER ? (
        <Notice tone="ok" icon={<CheckCircle2 className="h-4 w-4" />}>{L("Tu clave ya está escrita en todos los pasos. Guárdala: no se volverá a mostrar.", "Your key is already in every step. Save it: it will not be shown again.")}</Notice>
      ) : (
        <Notice tone="warn" icon={<KeyRound className="h-4 w-4" />}>
          <div className="flex flex-wrap items-center gap-3">
            <span>{L(`Los ejemplos usan «${PLACEHOLDER}». Crea una clave y se rellenará sola, o pega una que ya tengas:`, `Examples use “${PLACEHOLDER}”. Create a key and it will be filled in, or paste one you have:`)}</span>
            <Button size="sm" variant="primary" loading={busy} onClick={createKey}><KeyRound className="h-3.5 w-3.5" />{L("Crear clave", "Create key")}</Button>
            <Input className="h-8 max-w-[220px]" placeholder="sah-…" value={key} onChange={(e) => setKey(e.target.value.trim())} aria-label="API key" />
          </div>
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
