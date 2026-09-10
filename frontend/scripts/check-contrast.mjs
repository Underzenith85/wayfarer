#!/usr/bin/env node
/* Recomputes every calibrated pair in docs/08-accessibility.md straight from
   tokens.css. Run it after touching any ink or ground token.
   Usage:  node scripts/check-contrast.mjs [path/to/tokens.css]            */
import { readFileSync } from "node:fs";

const file =
  process.argv[2] ?? new URL("../src/styles/tokens.css", import.meta.url);
const css = readFileSync(file, "utf8");

const block = (re) =>
  Object.fromEntries(
    [...(css.match(re)?.[1] ?? "").matchAll(/(--[\w-]+):\s*([^;]+);/g)].map(
      (m) => [m[1], m[2].trim()],
    ),
  );
const day = block(/^:root \{([\s\S]*?)^\}/m);
const night = {
  ...day,
  ...block(/^:root\[data-theme="dark"\] \{([\s\S]*?)^\}/m),
};

const lin = (c) =>
  (c /= 255) <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
const lum = (hex) => {
  const h = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b);
};
const ratio = (a, b) => {
  const [x, y] = [lum(a), lum(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
};

/* [theme, foreground token, background token, minimum] */
const PAIRS = [
  ["day", "--ink-faint", "--paper", 4.5],
  ["day", "--ink-faint", "--page", 4.5],
  ["day", "--ink-faint", "--well", 4.5],
  ["day", "--ink-faint", "--wax-soft", 4.5],
  ["day", "--ink-soft", "--paper", 4.5],
  ["day", "--ink", "--paper", 4.5],
  ["day", "--wax", "--paper", 4.5],
  ["day", "--moss", "--paper", 4.5],
  ["day", "--amber", "--paper", 4.5],
  ["day", "--iron", "--paper", 4.5],
  ["day", "--page", "--wax", 4.5],
  ["night", "--ink-faint", "--page", 4.5],
  ["night", "--ink-faint", "--paper", 4.5],
  ["night", "--ink-soft", "--page", 4.5],
  ["night", "--ink", "--paper", 4.5],
  ["night", "--wax", "--page", 4.5],
  ["night", "--moss", "--page", 4.5],
  ["night", "--amber", "--page", 4.5],
  ["night", "--iron", "--page", 4.5],
  ["night", "--page", "--wax", 4.5],
];

let failed = 0,
  tightest = [null, Infinity];
for (const [theme, fg, bg, min] of PAIRS) {
  const src = theme === "day" ? day : night;
  if (!src[fg] || !src[bg]) {
    console.error(`missing token: ${fg} or ${bg} (${theme})`);
    failed++;
    continue;
  }
  const r = ratio(src[fg], src[bg]);
  const ok = r >= min;
  if (!ok) failed++;
  if (r < tightest[1]) tightest = [`${theme} ${fg} on ${bg}`, r];
  console.log(
    `${ok ? "ok  " : "FAIL"}  ${theme.padEnd(5)} ${fg.padEnd(13)} on ${bg.padEnd(11)} ${r.toFixed(2)}`,
  );
}
console.log(`\ntightest: ${tightest[0]} at ${tightest[1].toFixed(2)}`);
if (failed) {
  console.error(`\n${failed} pair(s) below 4.5:1`);
  process.exit(1);
}
console.log("all pairs clear 4.5:1");
