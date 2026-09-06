// Preserve the existing entrypoint; generation and checking share one implementation.
import process from "node:process";
process.argv.push("--check");
await import("./generate-contracts.mjs");
