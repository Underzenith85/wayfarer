import { readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import type { Plugin } from "vite";

/** Cache only build-owned shell resources; never runtime responses. */
export function pwaShell(): Plugin {
  return {
    name: "wayfarer-pwa-shell",
    apply: "build",
    generateBundle(_options, bundle) {
      const assets = Object.keys(bundle).filter((name) =>
        /\.(js|css)$/.test(name),
      );
      const version = createHash("sha256")
        .update(JSON.stringify(bundle))
        .update(readFileSync(new URL("../index.html", import.meta.url)))
        .update(
          readFileSync(
            new URL("../public/manifest.webmanifest", import.meta.url),
          ),
        )
        .update(
          readFileSync(new URL("../public/icon-192.png", import.meta.url)),
        )
        .update(
          readFileSync(new URL("../public/icon-512.png", import.meta.url)),
        )
        .digest("hex")
        .slice(0, 16);
      const shell = [
        "/index.html",
        "/manifest.webmanifest",
        "/icon-192.png",
        "/icon-512.png",
        ...assets.map((name) => `/${name}`),
      ];
      this.emitFile({
        type: "asset",
        fileName: "sw.js",
        source: `
const CACHE = "wayfarer-shell-${version}";
const SHELL = ${JSON.stringify(shell)};
self.addEventListener("install", event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)));
});
// Deliberately no skipWaiting/clients.claim: an update takes effect only after
// every old tab closes, so in-flight actions never cross application versions.
self.addEventListener("activate", event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(key => key.startsWith("wayfarer-shell-") && key !== CACHE).map(key => caches.delete(key)))));
});
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  // Mirrors the router: every page is served bare and under /c/<campaign>/.
  const route = new RegExp("^/(?:c/[^/]+/?)?(?:character|inventory|journal|campaign)?$");
  const key = event.request.mode === "navigate" && route.test(url.pathname)
    ? "/index.html" : (!url.search && SHELL.includes(url.pathname) ? url.pathname : null);
  if (key) event.respondWith(caches.open(CACHE).then(cache => cache.match(key)).then(response => response || fetch(event.request)));
});
`,
      });
    },
  };
}
