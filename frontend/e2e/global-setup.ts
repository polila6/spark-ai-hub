import { chromium, FullConfig } from "@playwright/test";

// Log in once and reuse the session (the hub rate-limits logins: 10 per user / 20 per IP per 5 min).
export default async function globalSetup(config: FullConfig) {
  const baseURL = config.projects[0].use.baseURL!;
  const browser = await chromium.launch();
  const page = await browser.newPage({ baseURL, locale: "es-ES" });
  await page.goto("/login");
  await page.getByLabel("Usuario").fill("admin");
  await page.getByLabel("Contraseña").fill(process.env.SAH_ADMIN_PASSWORD!);
  await page.getByRole("button", { name: "Iniciar sesión" }).click();
  await page.getByRole("heading", { name: "Panel de control" }).waitFor({ timeout: 30_000 });
  await page.context().storageState({ path: "/tmp/sah-e2e-state.json" });
  await browser.close();
}
