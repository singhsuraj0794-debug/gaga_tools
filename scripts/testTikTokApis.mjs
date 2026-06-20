// Test which TikTok download APIs are accessible with this RapidAPI key
const KEY = "e4f0168123msh21c83ca8fa786cap141b25jsn6b69c0e25be1";
const TT_URL = "https://www.tiktok.com/@khaby.lame/video/6978716717595721985";

const apis = [
  {
    name: "tiktok-video-no-watermark",
    url: `https://tiktok-video-no-watermark2.p.rapidapi.com/?url=${encodeURIComponent(TT_URL)}&hd=1`,
    host: "tiktok-video-no-watermark2.p.rapidapi.com",
  },
  {
    name: "tiktok-downloader-v2",
    url: `https://tiktok-downloader-download-tiktok-videos-without-watermark.p.rapidapi.com/index?url=${encodeURIComponent(TT_URL)}`,
    host: "tiktok-downloader-download-tiktok-videos-without-watermark.p.rapidapi.com",
  },
  {
    name: "social-media-video-downloader",
    url: `https://social-media-video-downloader.p.rapidapi.com/smvd/get/all?url=${encodeURIComponent(TT_URL)}`,
    host: "social-media-video-downloader.p.rapidapi.com",
  },
  {
    name: "all-in-one-social-media-downloader",
    url: `https://all-in-one-social-media-downloader.p.rapidapi.com/grab?url=${encodeURIComponent(TT_URL)}`,
    host: "all-in-one-social-media-downloader.p.rapidapi.com",
  },
];

for (const api of apis) {
  try {
    const res = await fetch(api.url, {
      headers: { "x-rapidapi-host": api.host, "x-rapidapi-key": KEY },
      signal: AbortSignal.timeout(8000),
    });
    const text = await res.text();
    const subscribed = !text.includes("not subscribed") && !text.includes("API doesn");
    console.log(`${api.name}: ${subscribed ? "✓ SUBSCRIBED" : "✗ not subscribed"} — ${text.slice(0, 80)}`);
  } catch (e) {
    console.log(`${api.name}: ERROR — ${e.message}`);
  }
}
