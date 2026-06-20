import { execFile } from "child_process";
import os from "os";
import path from "path";

const ytDlp = path.join(os.homedir(), "bin", "yt-dlp");

// Test 1: TikTok with impersonate
console.log("Test 1: TikTok with --impersonate chrome");
execFile(ytDlp, [
  "--no-playlist", "--get-url",
  "--impersonate", "chrome",
  "https://www.tiktok.com/@khaby.lame/video/6978716717595721985"
], { timeout: 30000 }, (err, stdout, stderr) => {
  if (err) {
    console.log("FAILED:", stderr.slice(0, 300));
    // Test 2: TikTok with cookies-from-browser safari
    console.log("\nTest 2: TikTok with --cookies-from-browser safari");
    execFile(ytDlp, [
      "--no-playlist", "--get-url",
      "--cookies-from-browser", "safari",
      "https://www.tiktok.com/@khaby.lame/video/6978716717595721985"
    ], { timeout: 30000 }, (err2, stdout2, stderr2) => {
      if (err2) {
        console.log("FAILED:", stderr2.slice(0, 300));
      } else {
        console.log("SUCCESS:", stdout2.slice(0, 100));
      }
    });
  } else {
    console.log("SUCCESS:", stdout.slice(0, 100));
  }
});
