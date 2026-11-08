"use client";
// «Leer al instante»: a message sent while the assistant is still thinking interrupts that generation (what it had reasoned
// so far is kept and handed back to it) and is read at once, instead of waiting for the step to end. Off = it joins the
// task at the next step, as before. The choice is remembered in this browser.
import { Zap } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { cx } from "@/components/ui";
import { useL } from "@/lib/i18n";

const KEY = "sah-steer";

export function useSteer(): [boolean, (v: boolean) => void] {
  const [on, setOn] = useState(true);
  useEffect(() => { try { const v = localStorage.getItem(KEY); if (v != null) setOn(v === "1"); } catch { /* private mode */ } }, []);
  const set = useCallback((v: boolean) => { setOn(v); try { localStorage.setItem(KEY, v ? "1" : "0"); } catch { /* private mode */ } }, []);
  return [on, set];
}

export function SteerToggle({ on, onChange, className }: { on: boolean; onChange: (v: boolean) => void; className?: string }) {
  const L = useL();
  return (
    <button type="button" role="switch" aria-checked={on} onClick={() => onChange(!on)}
      title={on ? L("Tu mensaje interrumpe el pensamiento actual (se conserva lo razonado) y lo lee al instante. Pulsa para que espere al siguiente paso.", "Your message interrupts the current thinking (what was reasoned is kept) and is read at once. Click to make it wait for the next step.")
        : L("Tu mensaje espera a que el asistente termine el paso actual. Pulsa para que lo lea al instante, sin perder lo razonado.", "Your message waits for the assistant to finish the current step. Click to have it read at once, without losing what was reasoned.")}
      className={cx("inline-flex h-7 items-center gap-1 rounded-full border px-2 text-[11px] transition", on ? "border-accent/50 bg-accent/12 text-accent" : "border-border text-muted hover:text-fg", className)}>
      <Zap className="h-3 w-3" />{L("Leer al instante", "Read instantly")}
    </button>
  );
}
