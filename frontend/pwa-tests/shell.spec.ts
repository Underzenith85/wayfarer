import { test, expect } from "@playwright/test";
test("production shell installs, excludes private routes, and reopens offline", async ({
  page,
  context,
}) => {
  await page.goto("/");
  await page.evaluate(() => navigator.serviceWorker.ready);
  await page.reload();
  await expect
    .poll(() => page.evaluate(() => !!navigator.serviceWorker.controller))
    .toBe(true);
  const manifest = await page.evaluate(async () =>
    (await fetch("/manifest.webmanifest")).json(),
  );
  expect(manifest.display).toBe("standalone");
  expect(manifest.icons.map((icon: { sizes: string }) => icon.sizes)).toEqual([
    "192x192",
    "512x512",
  ]);
  await page.evaluate(async () => {
    await fetch("/api/v1/private?token=secret");
    await fetch("/campaigns/private");
  });
  const keys = await page.evaluate(async () => {
    const cache = await caches.open(
      (await caches.keys()).find((key) => key.startsWith("wayfarer-shell-"))!,
    );
    return (await cache.keys()).map((request) => new URL(request.url).pathname);
  });
  expect(keys).toContain("/index.html");
  expect(
    keys.some(
      (key) => key.startsWith("/api/") || key.startsWith("/campaigns/"),
    ),
  ).toBe(false);
  await context.setOffline(true);
  await page.goto("/journal");
  await expect(
    page.getByRole("heading", { name: "Wayfarer", exact: true }),
  ).toBeVisible();
  await context.setOffline(false);
  await page.reload();
  await expect(
    page.getByRole("tab", { name: "New game", exact: true }),
  ).toBeVisible();
});
