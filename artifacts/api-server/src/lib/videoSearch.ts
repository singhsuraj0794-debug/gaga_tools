import axios from "axios";
import { logger } from "./logger";

export interface VideoResult {
  id: string;
  platform: string;
  title: string;
  url: string;
  embedUrl: string | null;
  thumbnailUrl: string | null;
  channelName: string | null;
  duration: string | null;
  viewCount: number | null;
  productId: string;
  productName: string;
}

function buildYouTubeEmbedUrl(videoId: string): string {
  return `https://www.youtube.com/embed/${videoId}`;
}

function formatDuration(iso: string): string {
  const match = iso.match(/PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?/);
  if (!match) return "";
  const h = match[1] ? `${match[1]}:` : "";
  const m = match[2] ? match[2].padStart(h ? 2 : 1, "0") : "0";
  const s = (match[3] || "0").padStart(2, "0");
  return `${h}${m}:${s}`;
}

// ─── YouTube via Data API v3 ───────────────────────────────────────────────

function buildSearchQuery(productName: string): string {
  // Use exact product name as primary query — no generic suffixes
  // Strip size/color variants in parentheses to keep the core name
  return productName.replace(/\s*\([^)]+\)\s*/g, " ").trim();
}

async function searchYouTubeWithApi(
  productName: string,
  productId: string,
  apiKey: string,
): Promise<VideoResult[]> {
  const query = buildSearchQuery(productName);
  const searchResp = await axios.get("https://www.googleapis.com/youtube/v3/search", {
    params: { key: apiKey, q: query, part: "snippet", type: "video", maxResults: 5 },
    timeout: 10000,
  });

  const items = searchResp.data.items || [];
  if (items.length === 0) return [];

  const videoIds = items.map((i: any) => i.id.videoId).join(",");
  let detailMap: Record<string, any> = {};
  try {
    const detailResp = await axios.get("https://www.googleapis.com/youtube/v3/videos", {
      params: { key: apiKey, id: videoIds, part: "contentDetails,statistics" },
      timeout: 10000,
    });
    for (const v of detailResp.data.items || []) detailMap[v.id] = v;
  } catch (e) {
    logger.warn({ e }, "Failed to fetch YouTube video details");
  }

  return items.map((item: any): VideoResult => {
    const videoId = item.id.videoId;
    const detail = detailMap[videoId];
    return {
      id: `yt-${videoId}`,
      platform: "youtube",
      title: item.snippet.title,
      url: `https://www.youtube.com/watch?v=${videoId}`,
      embedUrl: buildYouTubeEmbedUrl(videoId),
      thumbnailUrl:
        item.snippet.thumbnails?.high?.url ||
        item.snippet.thumbnails?.default?.url ||
        `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`,
      channelName: item.snippet.channelTitle || null,
      duration: detail ? formatDuration(detail.contentDetails?.duration || "") : null,
      viewCount: detail ? parseInt(detail.statistics?.viewCount || "0", 10) : null,
      productId,
      productName,
    };
  });
}

// ─── YouTube scrape fallback (no API key needed) ───────────────────────────

async function searchYouTubeScrape(
  productName: string,
  productId: string,
): Promise<VideoResult[]> {
  const query = encodeURIComponent(buildSearchQuery(productName));
  const resp = await axios.get(`https://www.youtube.com/results?search_query=${query}`, {
    headers: {
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.5",
    },
    timeout: 15000,
  });

  const html: string = resp.data;
  const match = html.match(/var ytInitialData = ({.*?});<\/script>/s);
  if (!match) return [];

  const data = JSON.parse(match[1]);
  const contents =
    data?.contents?.twoColumnSearchResultsRenderer?.primaryContents
      ?.sectionListRenderer?.contents || [];

  const videos: VideoResult[] = [];
  for (const section of contents) {
    const items = section?.itemSectionRenderer?.contents || [];
    for (const item of items) {
      const vr = item?.videoRenderer;
      if (!vr?.videoId) continue;
      const videoId: string = vr.videoId;
      const title: string = vr.title?.runs?.[0]?.text || productName;
      const channelName: string =
        vr.ownerText?.runs?.[0]?.text || vr.shortBylineText?.runs?.[0]?.text || null;
      const viewText: string = vr.viewCountText?.simpleText || vr.viewCountText?.runs?.[0]?.text || "";
      const viewMatch = viewText.match(/([\d,]+)/);
      const viewCount = viewMatch ? parseInt(viewMatch[1].replace(/,/g, ""), 10) : null;
      const duration: string = vr.lengthText?.simpleText || null;
      const thumbnail =
        vr.thumbnail?.thumbnails?.slice(-1)?.[0]?.url ||
        `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`;

      videos.push({
        id: `yt-${videoId}`,
        platform: "youtube",
        title,
        url: `https://www.youtube.com/watch?v=${videoId}`,
        embedUrl: buildYouTubeEmbedUrl(videoId),
        thumbnailUrl: thumbnail,
        channelName,
        duration,
        viewCount,
        productId,
        productName,
      });
      if (videos.length >= 5) break;
    }
    if (videos.length >= 5) break;
  }
  return videos;
}

async function searchYouTube(
  productName: string,
  productId: string,
  apiKey: string | undefined,
): Promise<VideoResult[]> {
  try {
    if (apiKey) return await searchYouTubeWithApi(productName, productId, apiKey);
    return await searchYouTubeScrape(productName, productId);
  } catch (err: any) {
    logger.warn({ err: err?.message, productName }, "YouTube API search failed, trying scrape");
    try {
      return await searchYouTubeScrape(productName, productId);
    } catch {
      return [];
    }
  }
}

// ─── Instagram via instagram120 (RapidAPI) ───────────────────────────────

async function searchInstagram(
  productName: string,
  productId: string,
  rapidApiKey: string,
): Promise<VideoResult[]> {
  try {
    // Build a clean hashtag from the product name (alphanumeric only, max 30 chars)
    const hashtag = productName
      .replace(/[^a-zA-Z0-9\s]/g, "")
      .split(/\s+/)
      .slice(0, 3)
      .join("")
      .toLowerCase()
      .slice(0, 30);

    const resp = await axios.post(
      "https://instagram120.p.rapidapi.com/api/instagram/hashtag",
      { hashtag },
      {
        headers: {
          "Content-Type": "application/json",
          "x-rapidapi-host": "instagram120.p.rapidapi.com",
          "x-rapidapi-key": rapidApiKey,
        },
        timeout: 12000,
      },
    );

    const items: any[] = resp.data?.items || resp.data?.data?.items || [];
    const videoItems = items.filter(
      (i: any) => i.media_type === 2 || i.is_video === true || i.video_url,
    );

    return videoItems.slice(0, 5).map((item: any): VideoResult => {
      const shortcode = item.code || item.shortcode || item.id || "";
      const caption = item.caption?.text || item.caption || "";
      return {
        id: `ig-${item.id || shortcode}`,
        platform: "instagram",
        title: (typeof caption === "string" ? caption.slice(0, 100) : productName) || productName,
        url: `https://www.instagram.com/p/${shortcode}/`,
        embedUrl: `https://www.instagram.com/p/${shortcode}/embed/`,
        thumbnailUrl:
          item.thumbnail_url ||
          item.image_versions2?.candidates?.[0]?.url ||
          item.display_url ||
          null,
        channelName: item.user?.username || item.owner?.username || null,
        duration: null,
        viewCount: item.play_count || item.view_count || item.video_view_count || null,
        productId,
        productName,
      };
    });
  } catch (err: any) {
    logger.warn({ err: err?.message, productName }, "Instagram search failed");
    return [];
  }
}

// ─── Facebook via facebook-scraper3 (RapidAPI) ───────────────────────────

async function searchFacebook(
  productName: string,
  productId: string,
  rapidApiKey: string,
): Promise<VideoResult[]> {
  try {
    const query = buildSearchQuery(productName);
    const resp = await axios.get("https://facebook-scraper3.p.rapidapi.com/search/videos", {
      params: { query, limit: 5 },
      headers: {
        "x-rapidapi-host": "facebook-scraper3.p.rapidapi.com",
        "x-rapidapi-key": rapidApiKey,
      },
      timeout: 12000,
    });

    const items: any[] = resp.data?.results || [];

    return items.slice(0, 5).map((item: any): VideoResult => {
      const videoId = String(item.video_id || "");
      const authorName = item.author?.name || null;
      // Parse view count from raw string like "2 hours ago · 11 views"
      let viewCount: number | null = null;
      const rawViews: string = item.time_and_views_raw || "";
      const viewMatch = rawViews.match(/([\d,]+)\s+views?/i);
      if (viewMatch) viewCount = parseInt(viewMatch[1].replace(/,/g, ""), 10);

      return {
        id: `fb-${videoId}`,
        platform: "facebook",
        title: item.title || item.description?.slice(0, 100) || productName,
        url: item.video_url || `https://www.facebook.com/watch/?v=${videoId}`,
        embedUrl: videoId
          ? `https://www.facebook.com/plugins/video.php?href=${encodeURIComponent(`https://www.facebook.com/watch/?v=${videoId}`)}&show_text=false&width=500`
          : null,
        thumbnailUrl: item.thumbnail || null,
        channelName: authorName,
        duration: null,
        viewCount,
        productId,
        productName,
      };
    });
  } catch (err: any) {
    logger.warn({ err: err?.message, productName }, "Facebook search failed");
    return [];
  }
}

// ─── Main export ──────────────────────────────────────────────────────────

export async function searchVideosForProducts(
  products: Array<{ id: string; name: string }>,
  platforms: string[] = ["youtube", "instagram", "facebook"],
): Promise<VideoResult[]> {
  // Support both GOOGLE_API_KEY and YOUTUBE_API_KEY env var names
  const googleKey = process.env.GOOGLE_API_KEY || process.env.YOUTUBE_API_KEY;
  const rapidApiKey = process.env.RAPIDAPI_KEY;

  if (!rapidApiKey) {
    logger.warn("RAPIDAPI_KEY not set — Instagram and Facebook search will be skipped");
  }
  if (!googleKey) {
    logger.warn("GOOGLE_API_KEY not set — YouTube will use HTML scrape fallback");
  }

  const allResults: VideoResult[] = [];

  for (const product of products) {
    const tasks: Promise<VideoResult[]>[] = [];

    if (platforms.includes("youtube")) {
      tasks.push(searchYouTube(product.name, product.id, googleKey));
    }
    if (platforms.includes("instagram") && rapidApiKey) {
      tasks.push(searchInstagram(product.name, product.id, rapidApiKey));
    }
    if (platforms.includes("facebook") && rapidApiKey) {
      tasks.push(searchFacebook(product.name, product.id, rapidApiKey));
    }

    const results = await Promise.allSettled(tasks);
    for (const r of results) {
      if (r.status === "fulfilled") allResults.push(...r.value);
    }
  }

  return allResults;
}
