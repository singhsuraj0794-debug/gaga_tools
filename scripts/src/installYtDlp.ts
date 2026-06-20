/**
 * installYtDlp.ts
 * Downloads the standalone yt-dlp macOS binary to ~/bin/yt-dlp
 */
import https from "https";
import fs from "fs";
import path from "path";
import os from "os";
import { execFileSync } from "child_process";

const BIN_DIR = path.join(os.homedir(), "bin");
const DEST = path.join(BIN_DIR, "yt-dlp");
const URL = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp_macos";

function download(url: string, dest: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(dest);
    const follow = (u: string) => {
      https.get(u, (res) => {
        if (res.statusCode === 301 || res.statusCode === 302) {
          follow(res.headers.location!);
          return;
        }
        if (res.statusCode !== 200) {
          reject(new Error(`HTTP ${res.statusCode}`));
          return;
        }
        const total = parseInt(res.headers["content-length"] || "0", 10);
        let received = 0;
        res.on("data", (chunk) => {
          received += chunk.length;
          if (total) process.stdout.write(`\r  ${Math.round((received / total) * 100)}%`);
        });
        res.pipe(file);
        file.on("finish", () => { file.close(); resolve(); });
        file.on("error", reject);
      }).on("error", reject);
    };
    follow(url);
  });
}

async function main() {
  if (!fs.existsSync(BIN_DIR)) fs.mkdirSync(BIN_DIR, { recursive: true });

  if (fs.existsSync(DEST)) {
    console.log(`yt-dlp already exists at ${DEST}`);
  } else {
    console.log(`Downloading yt-dlp to ${DEST}...`);
    await download(URL, DEST);
    console.log("\n  Download complete.");
  }

  fs.chmodSync(DEST, 0o755);
  const version = execFileSync(DEST, ["--version"]).toString().trim();
  console.log(`✓ yt-dlp ${version} ready at ${DEST}`);
}

main().catch((e) => { console.error("✗", e.message); process.exit(1); });
