import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { build as esbuild } from "esbuild";
import { rm } from "node:fs/promises";
import { cpSync, readdirSync, mkdirSync, existsSync } from "node:fs";

globalThis.require = createRequire(import.meta.url);

// Try to load esbuild-plugin-pino
let pinoPlugin;
try {
  const mod = await import("esbuild-plugin-pino");
  pinoPlugin = mod.default({ transports: ["pino-pretty"] });
} catch(e) {
  console.warn("pino plugin not available, continuing without it");
}

const artifactDir = path.dirname(fileURLToPath(import.meta.url));
const distDir = path.resolve(artifactDir, "dist");

await rm(distDir, { recursive: true, force: true });

await esbuild({
  entryPoints: [path.resolve(artifactDir, "src/index.ts")],
  platform: "node",
  bundle: true,
  format: "esm",
  outdir: distDir,
  outExtension: { ".js": ".mjs" },
  logLevel: "warning",
  external: [
    "*.node", "sharp", "better-sqlite3", "sqlite3", "canvas", "bcrypt",
    "argon2", "fsevents", "re2", "pg-native", "onnxruntime-node",
    "@tensorflow/*", "@prisma/client", "playwright", "puppeteer", "ws",
  ],
  sourcemap: "linked",
  plugins: pinoPlugin ? [pinoPlugin] : [],
  banner: {
    js: `import { createRequire as __bannerCrReq } from 'node:module';
import __bannerPath from 'node:path';
import __bannerUrl from 'node:url';
globalThis.require = __bannerCrReq(import.meta.url);
globalThis.__filename = __bannerUrl.fileURLToPath(import.meta.url);
globalThis.__dirname = __bannerPath.dirname(globalThis.__filename);`
  },
});

const allFiles = readdirSync(artifactDir);
const pyFiles = allFiles.filter(f => f.endsWith(".py"));
for (const f of pyFiles) {
  cpSync(path.resolve(artifactDir, f), path.resolve(distDir, f));
}

const jsonFiles = allFiles.filter(f => /^[a-z].*\.json$/.test(f) && !["package.json","tsconfig.json","vercel.json"].includes(f));
for (const f of jsonFiles) {
  cpSync(path.resolve(artifactDir, f), path.resolve(distDir, f));
}

console.log("BUILD OK — dist/:", readdirSync(distDir).length, "files");
