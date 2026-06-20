const KEY = "e4f0168123msh21c83ca8fa786cap141b25jsn6b69c0e25be1";
const HOST = "tiktok-download-video1.p.rapidapi.com";

// Use a hardcoded known-good TikTok URL format
// Get a real video ID from tiktok-api23 search
const searchRes = await fetch(
  "https://tiktok-api23.p.rapidapi.com/api/search/video?keyword=baby+toy&count=3&cursor=0",
  { headers: { "x-rapidapi-host": "tiktok-api23.p.rapidapi.com", "x-rapidapi-key": KEY } }
);
const searchData = await searchRes.json();
const items = searchData?.item_list || [];
console.log("Search found:", items.length, "videos");
if (items.length > 0) {
  const item = items[0];
  console.log("Item keys:", Object.keys(item).slice(0, 10));
  console.log("Author:", JSON.stringify(item.author).slice(0, 100));
  const videoId = item.id;
  // tiktok-api23 uses author.unique_id
  const authorId = item.author?.unique_id || item.author?.nickname || "user";
  const videoUrl = `https://www.tiktok.com/@${authorId}/video/${videoId}`;
  console.log("Video URL:", videoUrl);

  // Test getVideo directly
  const dlRes = await fetch(
    `https://${HOST}/getVideo?url=${encodeURIComponent(videoUrl)}&hd=1`,
    { headers: { "x-rapidapi-host": HOST, "x-rapidapi-key": KEY, "Content-Type": "application/json" } }
  );
  const dlData = await dlRes.json();
  console.log("\ngetVideo response:");
  console.log("code:", dlData.code, "msg:", dlData.msg);
  if (dlData.data?.play) console.log("play URL:", dlData.data.play.slice(0, 80));
  if (dlData.data?.hdplay) console.log("hdplay URL:", dlData.data.hdplay.slice(0, 80));
}
