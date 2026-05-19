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

async function searchYouTubeWithApi(
  productName: string,
  productId: string,
  apiKey: string,
): Promise<VideoResult[]> {
  const query = `${productName} review unboxing`;
  const searchResp = await axios.get("https://www.googleapis.com/youtube/v3/search", {
    params: {
      key: apiKey,
      q: query,
      part: "snippet",
      type: "video",
      maxResults: 5,
      relevanceLanguage: "en",
    },
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
    for (const v of detailResp.data.items || []) {
      detailMap[v.id] = v;
    }
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
      thumbnailUrl: item.snippet.thumbnails?.high?.url || item.snippet.thumbnails?.default?.url || null,
      channelName: item.snippet.channelTitle || null,
      duration: detail ? formatDuration(detail.contentDetails?.duration || "") : null,
      viewCount: detail ? parseInt(detail.statistics?.viewCount || "0", 10) : null,
      productId,
      productName,
    };
  });
}

async function searchYouTubeScrape(
  productName: string,
  productId: string,
): Promise<VideoResult[]> {
  const query = encodeURIComponent(`${productName} review unboxing`);
  const url = `https://www.youtube.com/results?search_query=${query}`;

  const resp = await axios.get(url, {
    headers: {
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
      Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
      if (!vr) continue;

      const videoId: string = vr.videoId || "";
      if (!videoId) continue;

      const title: string =
        vr.title?.runs?.[0]?.text || vr.title?.simpleText || productName;
      const channelName: string =
        vr.ownerText?.runs?.[0]?.text ||
        vr.shortBylineText?.runs?.[0]?.text ||
        null;

      const viewText: string =
        vr.viewCountText?.simpleText || vr.viewCountText?.runs?.[0]?.text || "";
      const viewMatch = viewText.match(/([\d,]+)/);
      const viewCount = viewMatch
        ? parseInt(viewMatch[1].replace(/,/g, ""), 10)
        : null;

      const lengthText: string =
        vr.lengthText?.simpleText || vr.lengthText?.accessibility?.accessibilityData?.label || null;

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
        duration: lengthText,
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
    if (apiKey) {
      return await searchYouTubeWithApi(productName, productId, apiKey);
    }
    return await searchYouTubeScrape(productName, productId);
  } catch (err: any) {
    logger.warn({ err: err?.message, productName }, "YouTube search failed, trying scrape fallback");
    if (apiKey) {
      try {
        return await searchYouTubeScrape(productName, productId);
      } catch {
        return [];
      }
    }
    return [];
  }
}

async function searchInstagram(
  productName: string,
  productId: string,
  rapidApiKey: string,
): Promise<VideoResult[]> {
  try {
    const resp = await axios.get("https://instagram-scraper-api2.p.rapidapi.com/v1/hashtag", {
      params: { hashtag: productName.replace(/\s+/g, "").toLowerCase() },
      headers: {
        "X-RapidAPI-Key": rapidApiKey,
        "X-RapidAPI-Host": "instagram-scraper-api2.p.rapidapi.com",
      },
      timeout: 10000,
    });

    const items = resp.data?.data?.items || [];
    const videoItems = items.filter((i: any) => i.media_type === 2 || i.is_video === true);

    return videoItems.slice(0, 5).map((item: any): VideoResult => {
      const shortcode = item.code || item.shortcode || "";
      return {
        id: `ig-${item.id || shortcode}`,
        platform: "instagram",
        title: item.caption?.text?.slice(0, 100) || productName,
        url: `https://www.instagram.com/p/${shortcode}/`,
        embedUrl: `https://www.instagram.com/p/${shortcode}/embed/`,
        thumbnailUrl: item.image_versions2?.candidates?.[0]?.url || item.thumbnail_url || null,
        channelName: item.user?.username || null,
        duration: null,
        viewCount: item.play_count || item.view_count || null,
        productId,
        productName,
      };
    });
  } catch (err: any) {
    logger.warn({ err: err?.message, productName }, "Instagram search failed");
    return [];
  }
}

async function searchTikTok(
  productName: string,
  productId: string,
  rapidApiKey: string,
): Promise<VideoResult[]> {
  try {
    const resp = await axios.get("https://tiktok-api23.p.rapidapi.com/api/search/video", {
      params: { keywords: productName, count: 5, cursor: 0, HD: 1 },
      headers: {
        "X-RapidAPI-Key": rapidApiKey,
        "X-RapidAPI-Host": "tiktok-api23.p.rapidapi.com",
      },
      timeout: 10000,
    });

    const items = resp.data?.data?.videos || resp.data?.video_list || [];

    return items.slice(0, 5).map((item: any): VideoResult => {
      const videoId = item.video_id || item.aweme_id || String(Math.random());
      const author = item.author?.unique_id || item.author?.nickname || "unknown";
      return {
        id: `tt-${videoId}`,
        platform: "tiktok",
        title: item.desc || item.title || productName,
        url: `https://www.tiktok.com/@${author}/video/${videoId}`,
        embedUrl: `https://www.tiktok.com/embed/v2/${videoId}`,
        thumbnailUrl: item.video?.origin_cover?.url_list?.[0] || item.video?.cover?.url_list?.[0] || null,
        channelName: author,
        duration: item.video?.duration ? `${Math.floor(item.video.duration / 1000)}s` : null,
        viewCount: item.statistics?.play_count || null,
        productId,
        productName,
      };
    });
  } catch (err: any) {
    logger.warn({ err: err?.message, productName }, "TikTok search failed");
    return [];
  }
}

export async function searchVideosForProducts(
  products: Array<{ id: string; name: string }>,
  platforms: string[] = ["youtube", "instagram", "tiktok"],
): Promise<VideoResult[]> {
  const youtubeKey = process.env.YOUTUBE_API_KEY;
  const rapidApiKey = process.env.RAPIDAPI_KEY;

  const allResults: VideoResult[] = [];

  for (const product of products) {
    const tasks: Promise<VideoResult[]>[] = [];

    if (platforms.includes("youtube")) {
      tasks.push(searchYouTube(product.name, product.id, youtubeKey));
    }
    if (platforms.includes("instagram") && rapidApiKey) {
      tasks.push(searchInstagram(product.name, product.id, rapidApiKey));
    }
    if (platforms.includes("tiktok") && rapidApiKey) {
      tasks.push(searchTikTok(product.name, product.id, rapidApiKey));
    }

    const results = await Promise.allSettled(tasks);
    for (const r of results) {
      if (r.status === "fulfilled") allResults.push(...r.value);
    }
  }

  return allResults;
}
