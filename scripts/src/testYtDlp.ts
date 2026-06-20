import { execFile } from "child_process";
import os from "os";
import path from "path";

const ytDlp = path.join(os.homedir(), "bin", "yt-dlp");

const urls = [
  "https://www.tiktok.com/@khaby.lame/video/6978716717595721985",
];

for (const url of urls) {
  console.log(`\nTesting: ${url}`);
  const child = execFile(ytDlp, [
    "--no-playlist",
    "--format", "best[ext=mp4]/best",
    "--get-url",
    "--cookies-from-browser", "chrome",
    url,
  ], { timeout: 30000 }, (err, stdout, stderr) => {
    if (err) {
      console.log("FAILED:", err.message);
      console.log("STDERR:", stderr.slice(0, 500));
    } else {
      console.log("SUCCESS, URL:", stdout.slice(0, 100));
    }
  });
}
