import { FileViewerProvider } from "@/components/file-viewer";
import { AppShell } from "@/components/shell";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return <FileViewerProvider><AppShell>{children}</AppShell></FileViewerProvider>;
}
