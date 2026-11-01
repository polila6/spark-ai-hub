"use client";
import { Clapperboard } from "lucide-react";
import { Studio } from "@/components/studio";
import { useI18n } from "@/lib/i18n";

export default function VideoPage() {
  const { t } = useI18n();
  return <Studio kind="video" title={t("vid.title")} subtitle={t("vid.subtitle")} icon={<Clapperboard className="h-5 w-5" />} />;
}
