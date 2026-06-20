import { cpSync, mkdirSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { execSync } from "node:child_process";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const frontendDist = path.resolve(root, "artifacts", "video-finder", "dist");
const outputDist = path.resolve(root, "dist");

execSync("pnpm run build:frontend", { cwd: root, stdio: "inherit" });

rmSync(outputDist, { recursive: true, force: true });
mkdirSync(outputDist, { recursive: true });
cpSync(frontendDist, outputDist, { recursive: true });

console.log("Created root dist for Vercel static output");
