import { expect, Page, test } from "@playwright/test";
import fs from "node:fs";

const SHOTS = process.env.SAH_SHOTS ?? "/shots";

async function login(page: Page) {
  // Session comes from global-setup storageState; just open the dashboard.
  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: "Panel de control" })).toBeVisible({ timeout: 30_000 });
}

test.beforeAll(() => fs.mkdirSync(SHOTS, { recursive: true }));

test("login is required and wrong password is rejected", async ({ browser }) => {
  const ctx = await browser.newContext({ storageState: { cookies: [], origins: [] } });
  const page = await ctx.newPage();
  await page.goto("/models");
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel("Usuario").fill("admin");
  await page.getByLabel("Contraseña").fill("wrong-password-123");
  await page.getByRole("button", { name: "Iniciar sesión" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Usuario o contraseña" })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/01-login.png` });
  await ctx.close();
});

test("dashboard shows measured telemetry", async ({ page }) => {
  await login(page);
  await expect(page.getByText("GPU GB10", { exact: true })).toBeVisible();
  await expect(page.getByText("Memoria unificada", { exact: true })).toBeVisible();
  await expect(page.getByText(/ollama/i).first()).toBeVisible();
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${SHOTS}/02-dashboard.png`, fullPage: true });
});

test("every navigation page renders without errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await login(page);
  const pages: [string, string][] = [
    ["/discover", "Descubrir modelos"], ["/models", "Biblioteca de modelos"], ["/playground", "AI Playground"],
    ["/compare", "Comparación de modelos"], ["/benchmarks", "Benchmarks"], ["/image", "Image Studio"], ["/video", "Video Studio"],
    ["/audio", "Audio Studio"], ["/science", "Laboratorio científico"], ["/jobs", "Trabajos"], ["/api-center", "Centro API"],
    ["/storage", "Almacenamiento"], ["/users", "Usuarios y permisos"], ["/monitoring", "Monitorización"], ["/settings", "Ajustes"],
  ];
  for (const [path, title] of pages) {
    await page.goto(path);
    await expect(page.getByRole("heading", { name: title }).first()).toBeVisible({ timeout: 20_000 });
    await page.waitForTimeout(1500);
    await page.screenshot({ path: `${SHOTS}/p${path.replace(/\//g, "_")}.png`, fullPage: true });
  }
  expect(errors).toEqual([]);
});

test("discover searches Hugging Face for real", async ({ page }) => {
  await login(page);
  await page.goto("/discover");
  await page.getByRole("textbox", { name: "Buscar" }).fill("whisper large v3");
  await page.getByRole("button", { name: "Buscar" }).click();
  await expect(page.getByText(/whisper-large-v3/i).first()).toBeVisible({ timeout: 30_000 });
  await page.getByText(/whisper-large-v3-turbo/i).first().click();
  await expect(page.getByText("Compatibilidad").first()).toBeVisible({ timeout: 30_000 });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `${SHOTS}/05-discover-detail.png`, fullPage: true });
});

test("playground streams a real answer", async ({ page }) => {
  await login(page);
  await page.goto("/playground");
  const sel = page.getByLabel("Modelo");
  await sel.selectOption({ label: /qwen3-4b-instruct/ as any }).catch(async () => {
    const opts = await sel.locator("option").allTextContents();
    await sel.selectOption({ label: opts.find((o) => o.includes("qwen3-4b"))! });
  });
  await page.getByPlaceholder(/Escribe un mensaje/).fill("¿Cuál es la capital de Francia? Responde en una frase.");
  await page.getByRole("button", { name: "Enviar" }).click();
  await expect(page.getByText(/París|Paris/).first()).toBeVisible({ timeout: 180_000 });
  await expect(page.getByText(/tok\/s/).first()).toBeVisible({ timeout: 60_000 });
  await page.screenshot({ path: `${SHOTS}/06-playground.png` });
});

test("language switch to English", async ({ page }) => {
  await login(page);
  await page.getByRole("button", { name: "Idioma" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  await page.getByRole("button", { name: "Language" }).click();
  await expect(page.getByRole("heading", { name: "Panel de control" })).toBeVisible();
});

test("mobile layout has a menu", async ({ browser }) => {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, storageState: "/tmp/sah-e2e-state.json" });
  const page = await ctx.newPage();
  await login(page);
  await page.getByRole("button", { name: "Open menu" }).click();
  await expect(page.getByRole("link", { name: "AI Playground" })).toBeVisible();
  await page.screenshot({ path: `${SHOTS}/07-mobile-menu.png` });
  await ctx.close();
});
