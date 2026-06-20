/**
 * testDownload.ts — tests a TikTok and YouTube download end-to-end
 */

async function pollStatus(jobId: string, label: string) {
  for (let i = 0; i < 30; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    const res = await fetch(`http://localhost:8080/api/videos/downloads/${jobId}/status`);
    const s = await res.json() as any;
    console.log(`  [${label}] ${i * 2}s: ${s.status} ${s.progress ?? 0}% ${s.error ?? ""}`);
    if (s.status === "completed" || s.status === "failed") {
      console.log(`  [${label}] FINAL: ${s.status} — file: ${s.fileName ?? "none"}`);
      return s;
    }
  }
}

async function startDownload(url: string, platform: string, title: string) {
  const res = await fetch("http://localhost:8080/api/videos/download", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url, platform, title }),
  });
  return res.json() as any;
}

async function main() {
  console.log("=== Download Test ===\n");

  // Test TikTok
  console.log("1. Starting TikTok download...");
  const ttJob = await startDownload(
    "https://www.tiktok.com/@khaby.lame/video/6978716717595721985",
    "tiktok",
    "Test TikTok"
  );
  console.log(`   jobId: ${ttJob.jobId}, status: ${ttJob.status}, error: ${ttJob.error ?? "none"}`);
  if (ttJob.jobId) await pollStatus(ttJob.jobId, "TikTok");

  // Test YouTube
  console.log("\n2. Starting YouTube download...");
  const ytJob = await startDownload(
    "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    "youtube",
    "Test YouTube"
  );
  console.log(`   jobId: ${ytJob.jobId}, status: ${ytJob.status}`);
  if (ytJob.jobId) await pollStatus(ytJob.jobId, "YouTube");
}

main().catch((e) => { console.error("Error:", e.message); process.exit(1); });
