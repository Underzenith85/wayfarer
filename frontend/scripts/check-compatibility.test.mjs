import { test } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, mkdirSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { assertCompatible, check } from "./check-compatibility.mjs";
const schema = {
  type: "object",
  properties: { description: { type: "string" }, id: { type: "string" } },
  required: ["id"],
  additionalProperties: false,
};
test("documentation annotations may change", () =>
  assert.doesNotThrow(() =>
    assertCompatible(
      { ...schema, description: "old" },
      { ...schema, description: "new" },
      "test",
    ),
  ));
test("removing an actual description property is breaking", () =>
  assert.throws(() =>
    assertCompatible(
      schema,
      { ...schema, properties: { id: { type: "string" } } },
      "test",
    ),
  ));
test("required fields, authorization, enums and event limits are frozen", () => {
  for (const [before, after] of [
    [schema, { ...schema, required: [] }],
    [{ security: [{ bearerAuth: [] }] }, { security: [] }],
    [{ enum: ["a", "b"] }, { enum: ["a"] }],
    [{ "x-limits": { max: 256 } }, { "x-limits": { max: 1024 } }],
  ])
    assert.throws(() => assertCompatible(before, after, "test"));
});

test("Git base comparison rejects breaking edits and requires a major migration record", () => {
  const root = mkdtempSync(path.join(tmpdir(), "wayfarer-contract-"));
  const git = (...args) =>
    execFileSync("git", ["-C", root, ...args], { encoding: "utf8" }).trim();
  const write = (name, value) => {
    const p = path.join(root, name);
    mkdirSync(path.dirname(p), { recursive: true });
    writeFileSync(p, JSON.stringify(value));
  };
  try {
    git("init", "-q");
    write("contracts/v1/schemas.json", schema);
    git("add", ".");
    git(
      "-c",
      "user.name=Contract Test",
      "-c",
      "user.email=contract@example.test",
      "commit",
      "-qm",
      "baseline",
    );
    const base = git("rev-parse", "HEAD");
    assert.equal(check(base, root), 1);
    write("contracts/v1/schemas.json", { ...schema, required: [] });
    assert.throws(() => check(base, root), /Frozen contract changed/);
    write("contracts/v1/schemas.json", schema);
    write("contracts/v2/schemas.json", { ...schema, required: [] });
    assert.throws(() => check(base, root), /migration record/);
    write("contracts/migrations/v2.json", {
      from_version: "v1",
      to_version: "v2",
      reason: "Consumer migration to optional identifiers.",
    });
    assert.equal(check(base, root), 1);
    rmSync(path.join(root, "contracts/v1/schemas.json"));
    assert.throws(() => check(base, root), /Frozen contract removed/);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
});
