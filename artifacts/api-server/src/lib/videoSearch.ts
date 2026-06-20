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
  relevanceScore?: number;
  directPlayUrl?: string | null;
}

export interface ImageSearchResult {
  title: string;
  link: string;
  thumbnailUrl: string;
  snippet: string;
}

async function reverseImageSearch(
  imageUrl: string,
  productId: string,
  productName: string,
  googleKey: string,
  cseId: string,
): Promise<ImageSearchResult[]> {
  try {
    const searchResp = await axios.get("https://www.googleapis.com/customsearch/v1", {
      params: {
        key: googleKey,
        cx: cseId,
        q: "",
        searchType: "image",
        imgUrl: imageUrl,
        num: 10,
      },
      timeout: 10000,
    });
    
    const items = searchResp.data.items || [];
    
    return items.map((item: any): ImageSearchResult => ({
      title: item.title || productName,
      link: item.link || "",
      thumbnailUrl: item.image?.thumbnailLink || "",
      snippet: item.snippet || "",
    }));
  } catch (err: any) {
    logger.warn({ err: err?.message, imageUrl }, "Reverse image search failed");
    return [];
  }
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

function normalizeText(text: string): string {
  return text.toLowerCase().replace(/[^\w\s]/g, " ").replace(/\s+/g, " ").trim();
}

function calculateRelevanceScore(video: VideoResult, productName: string): number {
  let score = 0;
  const normalizedTitle = normalizeText(video.title);
  const normalizedProductName = normalizeText(productName);
  
  // Exact match bonus
  if (normalizedTitle.includes(normalizedProductName)) {
    score += 50;
  }
  
  // Partial match bonus (check each word)
  const productWords = normalizedProductName.split(" ");
  let matchedWords = 0;
  for (const word of productWords) {
    if (word.length > 2 && normalizedTitle.includes(word)) {
      matchedWords++;
    }
  }
  score += (matchedWords / productWords.length) * 30;
  
  // View count bonus (logarithmic to avoid huge numbers dominating)
  if (video.viewCount) {
    score += Math.min(Math.log10(video.viewCount) * 5, 20);
  }
  
  return score;
}

function deduplicateVideos(videos: VideoResult[]): VideoResult[] {
  const seen = new Set<string>();
  return videos.filter(video => {
    if (seen.has(video.id)) {
      return false;
    }
    seen.add(video.id);
    return true;
  });
}

// ─── YouTube via Data API v3 ───────────────────────────────────────────────

function buildSearchQuery(productName: string, includeKeywords: boolean = true): string {
  let query = productName;
  
  // Strip size/color variants in parentheses
  query = query.replace(/\s*\([^)]+\)\s*/g, " ");
  
  // Add relevant keywords to improve search if requested
  if (includeKeywords) {
    query = `${query} product review unboxing`;
  }
  
  return query.trim();
}

async function searchYouTubeWithApi(
  productName: string,
  productId: string,
  apiKey: string,
): Promise<VideoResult[]> {
  // Try exact product name first
  let query = buildSearchQuery(productName, false);
  let searchResp = await axios.get("https://www.googleapis.com/youtube/v3/search", {
    params: { 
      key: apiKey, 
      q: query, 
      part: "snippet", 
      type: "video", 
      maxResults: 10,
      videoDuration: "short"
    },
    timeout: 10000,
  });

  let items = searchResp.data.items || [];
  
  // If no results, try with keywords
  if (items.length === 0) {
    query = buildSearchQuery(productName, true);
    searchResp = await axios.get("https://www.googleapis.com/youtube/v3/search", {
      params: { 
        key: apiKey, 
        q: query, 
        part: "snippet", 
        type: "video", 
        maxResults: 10,
        videoDuration: "short"
      },
      timeout: 10000,
    });
    items = searchResp.data.items || [];
  }
  
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
    const video: VideoResult = {
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
    video.relevanceScore = calculateRelevanceScore(video, productName);
    return video;
  });
}

// ─── YouTube scrape fallback (no API key needed) ───────────────────────────

async function searchYouTubeScrape(
  productName: string,
  productId: string,
): Promise<VideoResult[]> {
  // Try exact product name first
  let query = encodeURIComponent(buildSearchQuery(productName, false));
  let resp = await axios.get(`https://www.youtube.com/results?search_query=${query}`, {
    headers: {
      "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
      "Accept-Language": "en-US,en;q=0.5",
    },
    timeout: 15000,
  });

  let html: string = resp.data;
  let match = html.match(/var ytInitialData = ({.*?});<\/script>/s);
  let videos: VideoResult[] = [];

  if (match) {
    const data = JSON.parse(match[1]);
    const contents =
      data?.contents?.twoColumnSearchResultsRenderer?.primaryContents
        ?.sectionListRenderer?.contents || [];

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

        const video: VideoResult = {
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
        };
        video.relevanceScore = calculateRelevanceScore(video, productName);
        videos.push(video);
        if (videos.length >= 10) break;
      }
      if (videos.length >= 10) break;
    }
  }

  // If no results, try with keywords
  if (videos.length === 0) {
    query = encodeURIComponent(buildSearchQuery(productName, true));
    resp = await axios.get(`https://www.youtube.com/results?search_query=${query}`, {
      headers: {
        "User-Agent":
          "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.5",
      },
      timeout: 15000,
    });

    html = resp.data;
    match = html.match(/var ytInitialData = ({.*?});<\/script>/s);
    if (match) {
      const data = JSON.parse(match[1]);
      const contents =
        data?.contents?.twoColumnSearchResultsRenderer?.primaryContents
          ?.sectionListRenderer?.contents || [];

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

          const video: VideoResult = {
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
          };
          video.relevanceScore = calculateRelevanceScore(video, productName);
          videos.push(video);
          if (videos.length >= 10) break;
        }
        if (videos.length >= 10) break;
      }
    }
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

// ─── TikTok via tiktok-api23 (RapidAPI) ──────────────────────────────────

// ─── Facebook via facebook-scraper3 (RapidAPI) ───────────────────────────

async function searchFacebook(
  productName: string,
  productId: string,
  rapidApiKey: string,
): Promise<VideoResult[]> {
  try {
    // Try exact product name first
    let query = buildSearchQuery(productName, false);
    let resp = await axios.get("https://facebook-scraper3.p.rapidapi.com/search/videos", {
      params: { query, limit: 10 },
      headers: {
        "x-rapidapi-host": "facebook-scraper3.p.rapidapi.com",
        "x-rapidapi-key": rapidApiKey,
      },
      timeout: 12000,
    });

    let items: any[] = resp.data?.results || [];
    
    // If no results, try with keywords
    if (items.length === 0) {
      query = buildSearchQuery(productName, true);
      resp = await axios.get("https://facebook-scraper3.p.rapidapi.com/search/videos", {
        params: { query, limit: 10 },
        headers: {
          "x-rapidapi-host": "facebook-scraper3.p.rapidapi.com",
          "x-rapidapi-key": rapidApiKey,
        },
        timeout: 12000,
      });
      items = resp.data?.results || [];
    }

    return items.slice(0, 10).map((item: any): VideoResult => {
      const videoId = String(item.video_id || "");
      const authorName = item.author?.name || null;
      // Parse view count from raw string like "2 hours ago · 11 views"
      let viewCount: number | null = null;
      const rawViews: string = item.time_and_views_raw || "";
      const viewMatch = rawViews.match(/([\d,]+)\s+views?/i);
      if (viewMatch) viewCount = parseInt(viewMatch[1].replace(/,/g, ""), 10);

      const video: VideoResult = {
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
      video.relevanceScore = calculateRelevanceScore(video, productName);
      return video;
    });
  } catch (err: any) {
    logger.warn({ err: err?.message, productName }, "Facebook search failed");
    return [];
  }
}

// ─── TikTok via tiktok-api23 (RapidAPI) ──────────────────────────────────

async function searchTikTok(
  productName: string,
  productId: string,
  rapidApiKey: string,
): Promise<VideoResult[]> {
  try {
    // Try exact product name first
    let query = buildSearchQuery(productName, false);
    let resp = await axios.get(
      "https://tiktok-api23.p.rapidapi.com/api/search/video",
      {
        params: { keyword: query, count: 10, cursor: 0 },
        headers: {
          "x-rapidapi-host": "tiktok-api23.p.rapidapi.com",
          "x-rapidapi-key": rapidApiKey,
        },
        timeout: 12000,
      },
    );

    let items: any[] = resp.data?.item_list || resp.data?.data?.videos || [];
    
    // If no results, try with keywords
    if (items.length === 0) {
      query = buildSearchQuery(productName, true);
      resp = await axios.get(
        "https://tiktok-api23.p.rapidapi.com/api/search/video",
        {
          params: { keyword: query, count: 10, cursor: 0 },
          headers: {
            "x-rapidapi-host": "tiktok-api23.p.rapidapi.com",
            "x-rapidapi-key": rapidApiKey,
          },
          timeout: 12000,
        },
      );
      items = resp.data?.item_list || resp.data?.data?.videos || [];
    }

    return items.slice(0, 10).map((item: any): VideoResult => {
      const videoId = String(item.id || item.video_id || item.aweme_id || "");
      const author = item.author || {};
      const authorId = author.unique_id || author.sec_uid || "";
      const stats = item.stats || item.statistics || {};
      // Use a valid TikTok URL with a fallback username if needed
      const safeAuthorId = authorId || "video";
      const url = `https://www.tiktok.com/@${safeAuthorId}/video/${videoId}`;
      const video: VideoResult = {
        id: `tt-${videoId}`,
        platform: "tiktok",
        title: item.desc || item.title || productName,
        url,
        embedUrl: null,
        thumbnailUrl:
          item.video?.cover ||
          item.video?.dynamic_cover ||
          item.thumbnail ||
          null,
        channelName: author.nickname || author.unique_id || null,
        duration: item.video?.duration
          ? `${Math.round(item.video.duration)}s`
          : null,
        viewCount: stats.playCount ?? stats.play_count ?? item.play_count ?? null,
        productId,
        productName,
        directPlayUrl: item.video?.playAddr || item.video?.downloadAddr || null,
      };
      video.relevanceScore = calculateRelevanceScore(video, productName);
      return video;
    });
  } catch (err: any) {
    const status = err?.response?.status;
    if (status === 403 || status === 402 || status === 401) {
      logger.info({ productName }, "TikTok search skipped — RapidAPI plan does not cover tiktok-api23");
    } else {
      logger.warn({ err: err?.message, productName }, "TikTok search failed");
    }
    return [];
  }
}

// ─── Main export ──────────────────────────────────────────────────────────

export async function reverseImageSearchForProduct(
  product: { id: string; name: string; imageUrl: string },
): Promise<ImageSearchResult[]> {
  const googleKey = process.env.GOOGLE_API_KEY || process.env.YOUTUBE_API_KEY;
  const cseId = process.env.GOOGLE_CSE_ID;
  
  if (!googleKey || !cseId) {
    logger.warn("GOOGLE_API_KEY or GOOGLE_CSE_ID not set — reverse image search skipped");
    return [];
  }
  
  return await reverseImageSearch(
    product.imageUrl,
    product.id,
    product.name,
    googleKey,
    cseId,
  );
}

export async function searchVideosForProducts(
  products: Array<{ id: string; name: string }>,
  platforms: string[] = ["youtube", "tiktok"],
): Promise<{ results: VideoResult[]; warnings: string[] }> {
  // Support both GOOGLE_API_KEY and YOUTUBE_API_KEY env var names
  const googleKey = process.env.GOOGLE_API_KEY || process.env.YOUTUBE_API_KEY;
  const rapidApiKey = process.env.RAPIDAPI_KEY;

  const warnings: string[] = [];
  if (!rapidApiKey) {
    warnings.push("RAPIDAPI_KEY not set — TikTok and Facebook search will be skipped");
    logger.warn("RAPIDAPI_KEY not set — TikTok and Facebook search will be skipped");
  }
  if (!googleKey) {
    warnings.push("GOOGLE_API_KEY not set — YouTube will use HTML scrape fallback");
    logger.warn("GOOGLE_API_KEY not set — YouTube will use HTML scrape fallback");
  }

  const allResults: VideoResult[] = [];

  for (const product of products) {
    const tasks: Promise<VideoResult[]>[] = [];

    if (platforms.includes("youtube")) {
      tasks.push(searchYouTube(product.name, product.id, googleKey));
    }
    if (platforms.includes("tiktok") && rapidApiKey) {
      tasks.push((async () => {
        try {
          return await searchTikTok(product.name, product.id, rapidApiKey);
        } catch (err: any) {
          const status = err?.response?.status;
          if (status === 429) {
            const warning = "TikTok search rate limited — try again later or use YouTube only";
            if (!warnings.includes(warning)) warnings.push(warning);
            logger.warn({ productName: product.name, err: err.message }, "TikTok search rate limited");
          } else {
            const warning = "TikTok search failed — check API key or try again later";
            if (!warnings.includes(warning)) warnings.push(warning);
            logger.warn({ productName: product.name, err: err.message }, "TikTok search failed");
          }
          return [];
        }
      })());
    }
    if (platforms.includes("facebook") && rapidApiKey) {
      tasks.push(searchFacebook(product.name, product.id, rapidApiKey));
    }

    const results = await Promise.allSettled(tasks);
    for (const r of results) {
      if (r.status === "fulfilled") allResults.push(...r.value);
    }
  }

  // Deduplicate videos
  const deduplicated = deduplicateVideos(allResults);
  
  // Sort by relevance score (descending)
  deduplicated.sort((a, b) => (b.relevanceScore || 0) - (a.relevanceScore || 0));
  
  // Return top 20 results and warnings
  return { results: deduplicated.slice(0, 20), warnings };
}
