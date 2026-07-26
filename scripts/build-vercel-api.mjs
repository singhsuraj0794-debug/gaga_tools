import { cpSync, mkdirSync, rmSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));
const apiServerDir = path.resolve(root, "..", "artifacts", "api-server");
const distDir = path.resolve(apiServerDir, "dist");

rmSync(distDir, { recursive: true, force: true });
mkdirSync(distDir, { recursive: true });
cpSync(path.resolve(apiServerDir, "public"), distDir, { recursive: true });

const pyFiles = readdirSync(apiServerDir).filter(f => f.endsWith(".py"));
for (const f of pyFiles) {
  cpSync(path.resolve(apiServerDir, f), path.resolve(distDir, f));
}
if (pyFiles.length > 0) {
  console.log(`Copied ${pyFiles.length} Python scripts to dist/`);
}

console.log("Created api-server dist for Vercel static output");
