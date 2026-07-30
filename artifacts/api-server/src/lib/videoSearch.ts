import axios from "axios";
import { execFile } from "child_process";
import { promisify } from "util";
import path from "path";
import { logger } from "./logger";

const execFileAsync = promisify(execFile);
const QUERY_GENERATOR_SCRIPT = path.join(process.cwd(), "artifacts", "api-server", "dist", "_video_query_generator.py");

export interface VideoQuery {
  query: string;
  weight: number;
  reason: string;
}

/**
 * Call the Python DINOv2+CLIP query generator to get broader search queries.
 * Falls back to simple text expansion if the script fails.
 */
export async function generateVideoQueries(
  productName: string,
  imageUrl?: string,
): Promise<VideoQuery[]> {
  try {
    const input = imageUrl || "";
    const { stdout } = await execFileAsync("python3", [
      QUERY_GENERATOR_SCRIPT,
      input,
      productName,
    ], { timeout: 30000 });
    const data = JSON.parse(stdout);
    if (data.queries && data.queries.length > 0) {
      logger.info(
        { productName, category: data.category, queryCount: data.queries.length },
        "Video query generator: produced queries",
      );
      return data.queries;
    }
  } catch (err: any) {
    logger.warn({ err: err?.message, productName }, "Video query generator failed, using fallback");
  }
  // Fallback: simple text-based queries
  return [
    { query: productName, weight: 1.0, "reason": "product_name" },
  ];
}

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
  
  // Remove common filler words that make search too specific
  const fillerWords = /\b(for|indoor|outdoor|living\s*room|bedroom|kitchen|pack\s*of\s*\d+|set\s*of\s*\d+|piece|pieces|with|and|the|a|an|in|on|at|to|of|from|by|as|is|it|this|that|these|those|new|old|best|top|high|quality|premium|standard|generic|multi|color|colour|size|large|small|medium|big|little|mini|mega|super|ultra|pro|plus|extra)\b/gi;
  query = query.replace(fillerWords, " ");
  
  // Remove brand names if they're at the start (keep product type words)
  // e.g., "India Craft House Brothers Metal Floor Flower Planter" -> "Flower Planter"
  query = query.replace(/^[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\s+(?:Brothers?|Co|Company|Inc|Ltd|LLC|Store|Shop|House|Hub|World|Mart|Bazaar|Empire|Palace|Kingdom|Emporium)\s+/i, "");
  
  // Collapse multiple spaces
  query = query.replace(/\s+/g, " ").trim();
  
  // If query is still very long, take only the last few meaningful words
  // (product type is usually at the end: "Flower Planter", "Toy Car", etc.)
  const words = query.split(/\s+/);
  if (words.length > 6) {
    // Take last 4-5 words which usually contain the product type
    query = words.slice(-5).join(" ");
  }
  
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
  generatedQueries?: VideoQuery[],
): Promise<VideoResult[]> {
  // Use generated queries if available, otherwise fall back to buildSearchQuery
  const queries = generatedQueries && generatedQueries.length > 0
    ? generatedQueries.map(q => q.query)
    : [buildSearchQuery(productName, false), buildSearchQuery(productName, true)];

  let items: any[] = [];
  for (const query of queries.slice(0, 3)) { // Try top 3 queries
    try {
      const searchResp = await axios.get("https://www.googleapis.com/youtube/v3/search", {
        params: { 
          key: apiKey, 
          q: query, 
          part: "snippet", 
          type: "video", 
          maxResults: 10,
        },
        timeout: 10000,
      });
      items = searchResp.data.items || [];
      if (items.length > 0) break;
    } catch {
      continue;
    }
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

  return items.map((item: any): VideoResult | null => {
    const videoId = item.id.videoId;
    const detail = detailMap[videoId];
    const duration = detail ? formatDuration(detail.contentDetails?.duration || "") : null;
    
    // Filter to Shorts only (duration under 60s or unknown)
    if (duration && !isShortsDuration(duration)) return null;

    const video: VideoResult = {
      id: `yt-${videoId}`,
      platform: "youtube",
      title: item.snippet.title,
      url: `https://www.youtube.com/shorts/${videoId}`,
      embedUrl: buildYouTubeEmbedUrl(videoId),
      thumbnailUrl:
        item.snippet.thumbnails?.high?.url ||
        item.snippet.thumbnails?.default?.url ||
        `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`,
      channelName: item.snippet.channelTitle || null,
      duration: duration,
      viewCount: detail ? parseInt(detail.statistics?.viewCount || "0", 10) : null,
      productId,
      productName,
    };
    video.relevanceScore = calculateRelevanceScore(video, productName);
    return video;
  }).filter((v): v is VideoResult => v !== null);
}

/** Check if a duration string (e.g. "2:30" or "0:45") is under 60 seconds (Shorts). */
function isShortsDuration(duration: string): boolean {
  const parts = duration.split(":");
  if (parts.length === 2) {
    return parseInt(parts[0]) === 0 && parseInt(parts[1]) <= 60;
  }
  if (parts.length === 3) {
    return parseInt(parts[0]) === 0 && parseInt(parts[1]) === 0 && parseInt(parts[2]) <= 60;
  }
  return true;
}

/** Check if a YouTube duration string (e.g. "0:45" or "4:20") is under 60s. */
function isDurationShort(duration: string): boolean {
  if (!duration) return false;
  const parts = duration.split(":");
  if (parts.length === 2) {
    const mins = parseInt(parts[0]);
    const secs = parseInt(parts[1]);
    return mins === 0 && secs <= 60;
  }
  return false; // Longer formats are definitely not Shorts
}

// ─── YouTube scrape fallback (no API key needed) ───────────────────────────

async function searchYouTubeScrape(
  productName: string,
  productId: string,
  generatedQueries?: VideoQuery[],
): Promise<VideoResult[]> {
  const queries = generatedQueries && generatedQueries.length > 0
    ? generatedQueries.map(q => q.query)
    : [buildSearchQuery(productName, false), buildSearchQuery(productName, true)];

  const videos: VideoResult[] = [];
  const seenIds = new Set<string>();

  for (const rawQuery of queries.slice(0, 3)) {
    if (videos.length >= 10) break;
    try {
      const query = encodeURIComponent(rawQuery);
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
      if (!match) continue;

      const data = JSON.parse(match[1]);
      const contents =
        data?.contents?.twoColumnSearchResultsRenderer?.primaryContents
          ?.sectionListRenderer?.contents || [];

      for (const section of contents) {
        const items = section?.itemSectionRenderer?.contents || [];
        for (const item of items) {
          // Check regular video — include only if under 60s (likely a Short)
          const vr = item?.videoRenderer;
          if (vr?.videoId && !seenIds.has(vr.videoId)) {
            const duration = vr.lengthText?.simpleText || "";
            // Shorts are under 60s
            const isShort = isDurationShort(duration);
            if (isShort) {
              seenIds.add(vr.videoId);
              const videoId: string = vr.videoId;
              const title: string = vr.title?.runs?.[0]?.text || productName;
              const thumbnail = vr.thumbnail?.thumbnails?.slice(-1)?.[0]?.url ||
                `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`;
              const channelName = vr.ownerText?.runs?.[0]?.text || vr.shortBylineText?.runs?.[0]?.text || null;
              const viewText = vr.viewCountText?.simpleText || vr.viewCountText?.runs?.[0]?.text || "";
              const viewMatch = viewText.match(/([\d,]+)/);
              const viewCount = viewMatch ? parseInt(viewMatch[1].replace(/,/g, ""), 10) : null;
              const video: VideoResult = {
                id: `yt-${videoId}`, platform: "youtube", title,
                url: `https://www.youtube.com/shorts/${videoId}`,
                embedUrl: buildYouTubeEmbedUrl(videoId),
                thumbnailUrl: thumbnail, channelName, duration, viewCount,
                productId, productName,
              };
              video.relevanceScore = calculateRelevanceScore(video, productName);
              videos.push(video);
              if (videos.length >= 10) break;
            }
          }
          // Also collect from reelShelf (always Shorts)
          const reelShelf = item?.reelShelfRenderer;
          if (reelShelf?.items) {
            for (const reelItem of reelShelf.items) {
              const reel = reelItem?.reelItemRenderer;
              if (!reel?.videoId || seenIds.has(reel.videoId)) continue;
              seenIds.add(reel.videoId);
              const videoId: string = reel.videoId;
              const title: string = reel.headline?.simpleText || reel.videoTitle || productName;
              const thumbnail =
                reel.thumbnail?.thumbnails?.slice(-1)?.[0]?.url ||
                `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`;
              const video: VideoResult = {
                id: `yt-${videoId}`,
                platform: "youtube",
                title,
                url: `https://www.youtube.com/shorts/${videoId}`,
                embedUrl: `https://www.youtube.com/embed/${videoId}`,
                thumbnailUrl: thumbnail,
                channelName: null,
                duration: null,
                viewCount: null,
                productId,
                productName,
              };
              video.relevanceScore = calculateRelevanceScore(video, productName);
              videos.push(video);
              if (videos.length >= 10) break;
            }
          }
        }
        if (videos.length >= 10) break;
      }
    } catch {
      // This query failed, try next
    }
  }

  return videos;
}

async function searchYouTube(
  productName: string,
  productId: string,
  apiKey: string | undefined,
  generatedQueries?: VideoQuery[],
): Promise<VideoResult[]> {
  if (apiKey) {
    try {
      const results = await searchYouTubeWithApi(productName, productId, apiKey, generatedQueries);
      if (results.length > 0) return results;
      // API returned empty — fall through to scrape
      logger.info({ productName }, "YouTube API returned no results, trying scrape fallback");
    } catch (err: any) {
      logger.warn({ err: err?.message, productName }, "YouTube API search failed, trying scrape");
    }
  }
  try {
    return await searchYouTubeScrape(productName, productId, generatedQueries);
  } catch (err: any) {
    logger.warn({ err: err?.message, productName }, "YouTube scrape also failed");
    return [];
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
      logger.info({ productName }, "TikTok API (tiktok-api23) plan exhausted, trying web scrape fallback");
    } else {
      logger.warn({ err: err?.message, productName }, "TikTok API failed, trying web scrape fallback");
    }
    // Fallback: scrape TikTok search page
    try {
      return await searchTikTokScrape(productName, productId);
    } catch (scrapeErr: any) {
      logger.warn({ err: scrapeErr?.message, productName }, "TikTok scrape fallback also failed");
      return [];
    }
  }
}

// ─── TikTok scrape fallback (no API needed) ──────────────────────────────

async function searchTikTokScrape(
  productName: string,
  productId: string,
): Promise<VideoResult[]> {
  const videos: VideoResult[] = [];
  const seenIds = new Set<string>();

  const googleKey = process.env.GOOGLE_API_KEY;
  const cseId = process.env.GOOGLE_CSE_ID;

  // Strategy 1: Google Custom Search for TikTok videos
  if (googleKey && cseId) {
    const queries = [
      `site:tiktok.com ${buildSearchQuery(productName, false)}`,
      `site:tiktok.com ${buildSearchQuery(productName, true)}`,
    ];

    for (const rawQuery of queries) {
      if (videos.length >= 10) break;
      try {
        const resp = await axios.get("https://www.googleapis.com/customsearch/v1", {
          params: {
            key: googleKey,
            cx: cseId,
            q: rawQuery,
            num: 10,
          },
          timeout: 12000,
        });

        const items = resp.data?.items || [];
        for (const item of items) {
          if (videos.length >= 10) break;
          const link = item.link || "";
          const match = link.match(/tiktok\.com\/@([^/]+)\/video\/(\d+)/);
          if (!match) continue;
          const authorId = match[1];
          const videoId = match[2];
          if (seenIds.has(videoId)) continue;
          seenIds.add(videoId);

          const title = (item.title || productName)
            .replace(/\s*[-|]\s*TikTok.*$/i, "")
            .trim() || productName;

          const video: VideoResult = {
            id: `tt-${videoId}`,
            platform: "tiktok",
            title,
            url: `https://www.tiktok.com/@${authorId}/video/${videoId}`,
            embedUrl: null,
            thumbnailUrl: item.pagemap?.cse_thumbnail?.[0]?.src || null,
            channelName: authorId,
            duration: null,
            viewCount: null,
            productId,
            productName,
          };
          video.relevanceScore = calculateRelevanceScore(video, productName);
          videos.push(video);
        }
      } catch {
        // Search failed, try next query
      }
    }
  }

  // Strategy 2: Brave search fallback (if Google didn't return results)
  if (videos.length === 0) {
    const queries = [
      `${buildSearchQuery(productName, false)} site:tiktok.com`,
      `${buildSearchQuery(productName, true)} site:tiktok.com`,
    ];

    for (const rawQuery of queries) {
      if (videos.length >= 10) break;
      try {
        const resp = await axios.get(
          `https://search.brave.com/search?q=${encodeURIComponent(rawQuery)}&source=web`,
          {
            headers: {
              "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
              "Accept": "text/html",
              "Accept-Language": "en-US,en;q=0.5",
            },
            timeout: 12000,
          },
        );

        const html: string = resp.data;
        const videoIdRegex = /tiktok\.com\/@([^/"]+)\/video\/(\d{8,})/g;
        let match: RegExpExecArray | null;
        while ((match = videoIdRegex.exec(html)) !== null) {
          if (videos.length >= 10) break;
          const authorId = match[1];
          const videoId = match[2];
          if (seenIds.has(videoId)) continue;
          seenIds.add(videoId);

          const start = Math.max(0, match.index - 300);
          const end = Math.min(html.length, match.index + 300);
          const context = html.slice(start, end).replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
          const titleMatch = context.match(/([^|\n]{10,120})/);
          let title = titleMatch?.[0]?.trim() || productName;
          title = title.replace(/^\d+\s+results?\s*/i, "").replace(/\s*-\s*TikTok.*$/, "").trim();
          if (title.length < 10) title = productName;

          const video: VideoResult = {
            id: `tt-${videoId}`,
            platform: "tiktok",
            title,
            url: `https://www.tiktok.com/@${authorId}/video/${videoId}`,
            embedUrl: null,
            thumbnailUrl: null,
            channelName: authorId,
            duration: null,
            viewCount: null,
            productId,
            productName,
          };
          video.relevanceScore = calculateRelevanceScore(video, productName);
          videos.push(video);
        }
      } catch {
        // Search failed, try next query
      }
    }
  }

  return videos.slice(0, 10);
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
    // Generate broader search queries using DINOv2+CLIP query generator
    let generatedQueries: VideoQuery[] | undefined;
    try {
      generatedQueries = await generateVideoQueries(product.name);
      if (generatedQueries.length > 1) {
        logger.info(
          { productName: product.name, queries: generatedQueries.map(q => q.query) },
          "Generated video search queries",
        );
      }
    } catch {
      // Query generator failed — will use default queries
    }

    const tasks: Promise<VideoResult[]>[] = [];

    if (platforms.includes("youtube")) {
      tasks.push(searchYouTube(product.name, product.id, googleKey, generatedQueries));
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
