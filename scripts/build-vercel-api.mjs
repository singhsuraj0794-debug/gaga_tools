import { cpSync, mkdirSync, rmSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const apiServerDir = path.resolve(root, "..", "artifacts", "api-server");
const distDir = path.resolve(apiServerDir, "dist");

rmSync(distDir, { recursive: true, force: true });
mkdirSync(distDir, { recursive: true });
cpSync(path.resolve(apiServerDir, "public"), distDir, { recursive: true });

console.log("Created api-server dist for Vercel static output");
