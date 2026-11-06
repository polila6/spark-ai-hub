"use client";
// Stop a model load that is in progress (big vLLM loads take minutes): the container is stopped and its memory comes back.
import { Square } from "lucide-react";
import { useState } from "react";
import { Button, Confirm } from "@/components/ui";
import { api } from "@/lib/api";
import { useL } from "@/lib/i18n";
import { useToast } from "@/lib/providers";

export function CancelLoadButton({ modelId, alias, onDone, size = "sm" }: { modelId: string; alias: string; onDone?: () => void; size?: "sm" | "md" }) {
  const L = useL();
  const { push } = useToast();
  const [ask, setAsk] = useState(false);
  const [busy, setBusy] = useState(false);
  async function go() {
    setBusy(true);
    try {
      await api(`/api/v1/models/${modelId}/cancel-load`, { method: "POST" });
      push("ok", L(`Carga de ${alias} interrumpida`, `Load of ${alias} stopped`));
      setAsk(false);
      onDone?.();
    } catch (e: any) { push("error", e.message); setAsk(false); } finally { setBusy(false); }
  }
  return (
    <>
      <Button size={size} variant="outline" onClick={(e) => { e.stopPropagation(); setAsk(true); }}><Square className="h-3 w-3" />{L("Cancelar carga", "Cancel load")}</Button>
      <Confirm open={ask} onOpenChange={setAsk} busy={busy} danger onConfirm={go}
        title={L("¿Interrumpir la carga?", "Stop the load?")}
        text={L(`Se detiene la carga de ${alias} y se libera su memoria en unos segundos. El modelo no se volverá a cargar solo: tendrás que cargarlo tú cuando quieras.`,
          `The load of ${alias} is stopped and its memory is freed within seconds. The model will not load again by itself: you load it when you want.`)} />
    </>
  );
}
