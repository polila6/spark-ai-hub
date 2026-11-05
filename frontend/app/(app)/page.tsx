import { redirect } from "next/navigation";

// The hub opens on the chat; the dashboard lives at /dashboard (first item of the menu).
export default function Home() {
  redirect("/playground");
}
