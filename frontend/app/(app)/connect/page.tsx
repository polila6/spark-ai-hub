"use client";
import { Plug, ShieldCheck } from "lucide-react";
import { AndroidAppCard } from "@/components/android";
import { ConnectSteps } from "@/components/connect";
import { Guide } from "@/components/guide";
import { Card, CardBody, CardHeader, Notice, PageHeader } from "@/components/ui";
import { useL } from "@/lib/i18n";

export default function ConnectPage() {
  const L = useL();
  return (
    <div>
      <PageHeader title={L("Conectar herramientas", "Connect tools")} subtitle={L("OpenCode, Claude Code, VS Code, Cursor, Python… con los modelos del Spark y tu nube personal", "OpenCode, Claude Code, VS Code, Cursor, Python… with the Spark models and your personal cloud")} icon={<Plug className="h-5 w-5" />} />
      <Guide id="connect" />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]">
        <Card><CardBody><ConnectSteps /></CardBody></Card>
        <div className="space-y-4">
          <AndroidAppCard />
          <Card><CardHeader title={L("¿Qué ofrece cada conexión?", "What does each connection offer?")} /><CardBody className="space-y-3 text-sm">
            <p><b>/v1</b> · {L("API compatible con OpenAI: chat, streaming, herramientas (tools), visión y embeddings. La usan OpenCode, Continue, Cline, Aider, Cursor, Python, LangChain…", "OpenAI-compatible API: chat, streaming, tools, vision and embeddings. Used by OpenCode, Continue, Cline, Aider, Cursor, Python, LangChain…")}</p>
            <p><b>/v1/messages</b> · {L("API compatible con Anthropic: para Claude Code y SDKs de Anthropic.", "Anthropic-compatible API: for Claude Code and Anthropic SDKs.")}</p>
            <p><b>/mcp</b> · {L("Servidor MCP con tu nube personal: buscar en tus documentos, leer archivos, consultar datos con SQL, leer chats anteriores, memoria y generar imágenes.", "MCP server with your personal cloud: search your documents, read files, query data with SQL, read previous chats, memory and image generation.")}</p>
          </CardBody></Card>
          <Notice tone="info" icon={<ShieldCheck className="h-4 w-4" />}>{L("Todo viaja cifrado por Tailscale y se queda en el Spark. Cada clave actúa como tú: no la compartas y revócala en Centro API si la pierdes.", "Everything travels encrypted through Tailscale and stays on the Spark. Each key acts as you: do not share it and revoke it in API centre if you lose it.")}</Notice>
          <Notice tone="muted">{L("¿Qué modelo elegir? El principal: está siempre cargado, tiene «tools» y responde al instante. Úsalo para todo (también autocompletar y tareas rápidas): cargar un segundo modelo «ligero» ralentiza mucho el principal. Otros modelos se cargan bajo demanda (la primera petición tarda minutos).", "Which model? The main one: always loaded, has “tools” and answers instantly. Use it for everything (autocomplete and quick tasks too): loading a second “light” model slows the main one down a lot. Other models load on demand (the first request takes minutes).")}</Notice>
        </div>
      </div>
    </div>
  );
}
