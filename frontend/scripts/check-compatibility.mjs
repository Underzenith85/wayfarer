import { execFileSync } from "node:child_process";
import { readFileSync, existsSync } from "node:fs";
import { URL, fileURLToPath } from "node:url";
import path from "node:path";
import process from "node:process";
import console from "node:console";
export function semantic(value) {
  if (Array.isArray(value)) return value.map(semantic);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value)
        .filter(
          ([key, v]) =>
            !(
              ["description", "title", "$comment"].includes(key) &&
              typeof v === "string"
            ),
        )
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([k, v]) => [k, semantic(v)]),
    );
  return value;
}
export function assertCompatible(before, after, label) {
  if (JSON.stringify(semantic(before)) !== JSON.stringify(semantic(after)))
    throw new Error(
      `Frozen contract changed: ${label}. Preserve the existing major and add a reviewed version + migration record.`,
    );
}
export function check(base, root) {
  if (!/^[a-f0-9]{40}$/.test(base))
    throw new Error("CONTRACT_BASE_SHA must be a full commit SHA");
  const git = (...args) =>
    execFileSync("git", ["-C", root, ...args], {
      encoding: "utf8",
      maxBuffer: 16 * 1024 * 1024,
    });
  const pattern = /^contracts\/v\d+\/(openapi|schemas|events\.schema)\.json$/;
  const previous = git("ls-tree", "-r", "--name-only", base)
    .trim()
    .split("\n")
    .filter((x) => pattern.test(x));
  for (const name of previous) {
    const p = path.join(root, name);
    if (!existsSync(p)) throw new Error(`Frozen contract removed: ${name}`);
    assertCompatible(
      JSON.parse(git("show", `${base}:${name}`)),
      JSON.parse(readFileSync(p, "utf8")),
      name,
    );
  }
  const current = git(
    "ls-files",
    "--cached",
    "--others",
    "--exclude-standard",
    "contracts",
  )
    .trim()
    .split("\n");
  for (const name of current.filter(
    (x) => pattern.test(x) && !previous.includes(x),
  )) {
    const major = name.split("/")[1];
    if (previous.some((x) => x.startsWith(`contracts/${major}/`)))
      throw new Error(`New frozen surface inside existing ${major}: ${name}`);
    const migration = path.join(root, "contracts/migrations", `${major}.json`);
    if (!existsSync(migration))
      throw new Error(`New major requires migration record: ${major}`);
    const record = JSON.parse(readFileSync(migration, "utf8"));
    if (
      record.to_version !== major ||
      typeof record.reason !== "string" ||
      record.reason.length < 20 ||
      typeof record.from_version !== "string"
    )
      throw new Error("Incomplete migration record");
  }
  return previous.length;
}
if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const root = fileURLToPath(new URL("../../", import.meta.url));
  const base = process.env.CONTRACT_BASE_SHA;
  if (!base)
    throw new Error(
      "Set CONTRACT_BASE_SHA to the PR base commit (not the head).",
    );
  console.log(
    `Checked ${check(base, root)} frozen contract documents against ${base}`,
  );
}
