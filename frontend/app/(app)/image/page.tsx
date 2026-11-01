"use client";
import { Image as ImageIcon } from "lucide-react";
import { Studio } from "@/components/studio";
import { useI18n } from "@/lib/i18n";

export default function ImagePage() {
  const { t } = useI18n();
  return <Studio kind="image" title={t("img.title")} subtitle={t("img.subtitle")} icon={<ImageIcon className="h-5 w-5" />} />;
}
