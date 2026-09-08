#!/usr/bin/env node
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../src/", import.meta.url));
const files = [];
const visit = (directory) => {
  for (const name of readdirSync(directory)) {
    const path = join(directory, name);
    if (statSync(path).isDirectory()) visit(path);
    else if (/\.(css|tsx)$/.test(name)) files.push(path);
  }
};
visit(root);

const failures = [];
const withoutComments = (source) =>
  source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");

for (const file of files) {
  const display = relative(root, file);
  const source = withoutComments(readFileSync(file, "utf8"));
  const isTokens = display === "styles/tokens.css";

  if (
    file.endsWith(".css") &&
    !isTokens &&
    /#[0-9a-f]{3,8}(?![0-9a-f])/gi.test(source)
  )
    failures.push(`${display}: colour literals belong in styles/tokens.css`);
  if (
    file.endsWith(".css") &&
    !isTokens &&
    /(?:rgba?|hsla?)\s*\(/gi.test(source)
  )
    failures.push(`${display}: colour functions belong in styles/tokens.css`);

  if (file.endsWith(".tsx")) {
    if (
      /(?:fill|stroke|color|backgroundColor)\s*=\s*(?:["']|\{["'])#[0-9a-f]{3,8}/i.test(
        source,
      ) ||
      /style\s*=\s*\{\{[^}]*#[0-9a-f]{3,8}/i.test(source)
    )
      failures.push(`${display}: component colours must use design tokens`);
    if (/(?:repeating-)?(?:linear|radial)-gradient\s*\(/i.test(source))
      failures.push(`${display}: gradients are not allowed in components`);
    if (/[\u{1F300}-\u{1FAFF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}]/u.test(source))
      failures.push(`${display}: use an accessible ornament instead of emoji`);
  }

  if (file.endsWith(".css")) {
    const gradient = /(?:repeating-)?(?:linear|radial)-gradient\s*\(/gi;
    for (const match of source.matchAll(gradient)) {
      const before = source.slice(0, match.index);
      const open = before.lastIndexOf("{");
      const close = before.lastIndexOf("}");
      const selector = before.slice(close + 1, open).trim();
      if (
        !/(^|,)\s*(body|\.play-workspace|main\.leaf|\.gm-message|\.deckle)(\s|,|$)/.test(
          selector,
        )
      )
        failures.push(
          `${display}: gradient is outside a paper structure selector (${selector})`,
        );
    }

    for (const match of source.matchAll(/border-radius\s*:\s*([^;}]+)/gi)) {
      const value = match[1].trim();
      if (!["var(--radius)", "var(--radius-pill)", "0", "50%"].includes(value))
        failures.push(`${display}: unsupported border radius ${value}`);
    }
  }
}

const contrast = spawnSync(
  process.execPath,
  [new URL("./check-contrast.mjs", import.meta.url).pathname],
  { encoding: "utf8" },
);
if (contrast.status !== 0) {
  failures.push("The calibrated colour pairs do not all clear 4.5:1");
  process.stderr.write(contrast.stdout);
  process.stderr.write(contrast.stderr);
}

if (failures.length) {
  for (const failure of failures) console.error(`design: ${failure}`);
  process.exit(1);
}
console.log(`Field Journal checks passed for ${files.length} source files.`);
