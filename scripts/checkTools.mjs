import { execFile } from "child_process";

const tools = ["ffmpeg", "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"];
for (const t of tools) {
  execFile(t, ["-version"], { timeout: 5000 }, (err, stdout) => {
    if (!err) console.log(`${t}: FOUND — ${stdout.split("\n")[0]}`);
    else console.log(`${t}: not found`);
  });
}
