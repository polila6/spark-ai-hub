"use client";
import { Download, Smartphone } from "lucide-react";
import { useEffect, useState } from "react";
import useSWR from "swr";
import { GuideBody } from "./guide";
import { GUIDES } from "@/lib/guides";
import { useL } from "@/lib/i18n";
import { bytes } from "@/lib/format";
import { Button, Card, CardBody, CardHeader, Code } from "./ui";

/** Download card + install steps for the Android app (hidden inside the app itself). */
export function AndroidAppCard() {
  const L = useL();
  const { data } = useSWR("/api/v1/app/android/info");
  const [inApp, setInApp] = useState(false);
  const [url, setUrl] = useState("");
  useEffect(() => { setInApp(navigator.userAgent.includes("SparkAIHubAndroid")); setUrl(`${window.location.origin}/api/v1/app/android`); }, []);
  if (inApp || !data?.available) return null;
  return (
    <Card>
      <CardHeader title={L("App para Android", "Android app")} subtitle={`v${data.version} · ${bytes(data.size_bytes)} · Android ${data.min_android}`} icon={<Smartphone className="h-4 w-4" />}
        actions={<a href={data.url}><Button size="sm" variant="primary"><Download className="h-3.5 w-3.5" />{L("Descargar APK", "Download APK")}</Button></a>} />
      <CardBody className="space-y-3">
        <GuideBody g={GUIDES.android} />
        <p className="text-xs text-muted">{L("Dirección para abrir desde el navegador del móvil (con Tailscale conectado):", "Address to open from the phone browser (with Tailscale connected):")}</p>
        <Code>{url}</Code>
        <p className="break-all text-[10px] text-subtle">SHA-256: {data.sha256}</p>
      </CardBody>
    </Card>
  );
}
