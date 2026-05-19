import React, { useState, useEffect, useCallback } from "react";
import {
  useScrapeProducts,
  useSearchVideos,
  useDownloadVideo,
  useListDownloads,
  getScrapeProductsQueryKey,
  getListDownloadsQueryKey,
} from "@workspace/api-client-react";
import type {
  Product,
  VideoResult,
  DownloadJob,
  DownloadedFile,
  VideoSearchResult,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Progress } from "@/components/ui/progress";
import {
  Play,
  Download,
  Search,
  RefreshCw,
  AlertCircle,
  FileVideo,
  ChevronRight,
  ChevronLeft,
} from "lucide-react";
import { useToast } from "@/hooks/use-toast";

// ─── DownloadItem ──────────────────────────────────────────────────────────

type DownloadEntry = DownloadJob | DownloadedFile;

function isDownloadJob(entry: DownloadEntry): entry is DownloadJob {
  return "status" in entry;
}

interface DownloadItemProps {
  job: DownloadEntry;
  onComplete?: (jobId: string) => void;
}

function DownloadItem({ job, onComplete }: DownloadItemProps) {
  const queryClient = useQueryClient();

  // DownloadedFile objects have no `status` — infer "completed" when filePath present
  const inferredStatus: string = isDownloadJob(job)
    ? job.status
    : job.filePath
    ? "completed"
    : "pending";

  const initialProgress = isDownloadJob(job) ? (job.progress ?? 0) : 100;
  const initialFilePath = isDownloadJob(job) ? (job.filePath ?? null) : job.filePath;
  const initialFileSize = isDownloadJob(job) ? (job.fileSize ?? null) : job.fileSize;

  const [liveStatus, setLiveStatus] = useState<string>(inferredStatus);
  const [progress, setProgress] = useState<number>(initialProgress);
  const [filePath, setFilePath] = useState<string | null>(initialFilePath);
  const [fileSize, setFileSize] = useState<number | null>(initialFileSize);

  const isDone = liveStatus === "completed" || liveStatus === "failed";

  useEffect(() => {
    if (isDone) return;

    const es = new EventSource(`/api/videos/downloads/${job.jobId}/progress`);

    es.onmessage = (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data) as {
          status?: string;
          progress?: number;
          filePath?: string;
          fileSize?: number;
          error?: string;
        };
        if (data.error) {
          es.close();
          return;
        }
        if (data.status) setLiveStatus(data.status);
        if (data.progress !== undefined) setProgress(data.progress);
        if (data.filePath) setFilePath(data.filePath);
        if (data.fileSize) setFileSize(data.fileSize);

        if (data.status === "completed" || data.status === "failed") {
          es.close();
          queryClient.invalidateQueries({ queryKey: getListDownloadsQueryKey() });
          onComplete?.(job.jobId);
        }
      } catch {}
    };

    es.onerror = () => es.close();
    return () => es.close();
  }, [job.jobId, isDone]);

  const isCompleted = liveStatus === "completed";
  const isDownloading = liveStatus === "downloading" || liveStatus === "pending";
  const title = job.title ?? ("fileName" in job ? job.fileName : undefined) ?? "Video";
  const platform = job.platform ?? "unknown";
  const errorMsg = isDownloadJob(job) ? job.error : undefined;

  return (
    <div className="flex flex-col gap-2 p-3 border rounded-md bg-card shadow-sm text-sm">
      <div className="flex justify-between items-start gap-2">
        <div className="font-medium truncate" title={title}>
          {title}
        </div>
        <Badge variant="outline" className="text-[10px] shrink-0 uppercase">
          {platform}
        </Badge>
      </div>

      <div className="flex items-center justify-between mt-1 text-muted-foreground text-xs">
        <span className="capitalize">{liveStatus}</span>
        {fileSize && (
          <span>{(fileSize / (1024 * 1024)).toFixed(1)} MB</span>
        )}
      </div>

      {isDownloading && (
        <Progress value={progress} className="h-1.5 mt-1" />
      )}

      {isCompleted && filePath && (
        <Button
          variant="secondary"
          size="sm"
          className="w-full mt-2 h-7 text-xs"
          asChild
        >
          <a
            href={`/api/videos/downloads/${job.jobId}/play`}
            target="_blank"
            rel="noopener noreferrer"
          >
            <Play className="w-3 h-3 mr-1.5" /> Play
          </a>
        </Button>
      )}

      {liveStatus === "failed" && (
        <p className="text-xs text-destructive mt-1">{errorMsg || "Download failed"}</p>
      )}
    </div>
  );
}

// ─── VideoPreview (TikTok / Instagram in-app player) ──────────────────────

function VideoPreview({ video }: { video: VideoResult }) {
  const [playing, setPlaying] = useState(false);
  const previewUrl = `/api/videos/preview?url=${encodeURIComponent(video.url)}`;

  if (playing) {
    return (
      <div className="aspect-video bg-black relative">
        <video
          src={previewUrl}
          className="absolute inset-0 w-full h-full"
          controls
          autoPlay
          onError={() => setPlaying(false)}
        />
      </div>
    );
  }

  return (
    <div className="aspect-video bg-muted relative border-b group">
      {video.thumbnailUrl ? (
        <img
          src={video.thumbnailUrl}
          alt={video.title}
          className="w-full h-full object-cover"
        />
      ) : (
        <div className="w-full h-full flex items-center justify-center">
          <FileVideo className="w-8 h-8 text-muted-foreground/30" />
        </div>
      )}
      <div className="absolute inset-0 bg-black/40 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
        <Button
          variant="secondary"
          size="icon"
          className="rounded-full w-12 h-12"
          onClick={() => setPlaying(true)}
        >
          <Play className="w-5 h-5 ml-0.5" />
        </Button>
      </div>
    </div>
  );
}

// ─── WarningBanner ─────────────────────────────────────────────────────────

function WarningBanner({ warnings }: { warnings: string[] }) {
  if (!warnings.length) return null;
  return (
    <div className="mx-4 mt-3 rounded-md border border-amber-300 bg-amber-50 dark:bg-amber-950/30 dark:border-amber-700 px-3 py-2 flex gap-2 items-start">
      <AlertCircle className="w-4 h-4 text-amber-600 dark:text-amber-400 shrink-0 mt-0.5" />
      <div className="text-xs text-amber-800 dark:text-amber-300 space-y-0.5">
        {warnings.map((w, i) => (
          <p key={i}>{w}</p>
        ))}
      </div>
    </div>
  );
}

// ─── Dashboard ─────────────────────────────────────────────────────────────

const platformColors: Record<string, string> = {
  youtube: "bg-red-500/10 text-red-600 border-red-500/20",
  instagram: "bg-fuchsia-500/10 text-fuchsia-600 border-fuchsia-500/20",
  facebook: "bg-blue-600/10 text-blue-600 border-blue-600/20",
  tiktok:
    "bg-slate-800/10 text-slate-800 border-slate-800/20 dark:bg-white/10 dark:text-white dark:border-white/20",
};

export default function Dashboard() {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  const [page, setPage] = useState(1);
  const [selectedProducts, setSelectedProducts] = useState<Product[]>([]);
  const [forceRefresh, setForceRefresh] = useState(false);
  const [searchResults, setSearchResults] = useState<VideoSearchResult | null>(null);
  const [searchWarnings, setSearchWarnings] = useState<string[]>([]);

  // Active (pending/downloading) jobs — shown immediately after starting
  const [activeJobs, setActiveJobs] = useState<DownloadJob[]>([]);

  // ── Queries & Mutations ────────────────────────────────────────────────

  const scrapeParams = { page, refresh: forceRefresh || undefined };
  const { data: productsData, isLoading: isLoadingProducts } = useScrapeProducts(
    scrapeParams,
    {
      query: {
        staleTime: 0,
        queryKey: getScrapeProductsQueryKey(scrapeParams),
      },
    }
  );

  const searchVideosMutation = useSearchVideos();
  const downloadVideoMutation = useDownloadVideo();
  const { data: downloadsData } = useListDownloads();

  // Reset forceRefresh once data arrives
  useEffect(() => {
    if (forceRefresh && productsData) {
      setForceRefresh(false);
    }
  }, [productsData, forceRefresh]);

  // ── Handlers ──────────────────────────────────────────────────────────

  const handleScrape = () => {
    setPage(1);
    setForceRefresh(true);
  };

  const handleProductToggle = (product: Product, checked: boolean) => {
    if (checked) {
      if (selectedProducts.length >= 5) {
        toast({ title: "Maximum 5 products selected", variant: "destructive" });
        return;
      }
      setSelectedProducts((prev) => [...prev, product]);
    } else {
      setSelectedProducts((prev) => prev.filter((p) => p.id !== product.id));
    }
  };

  const handleSearch = () => {
    if (selectedProducts.length === 0) return;
    setSearchWarnings([]);
    searchVideosMutation.mutate(
      {
        data: {
          products: selectedProducts.map((p) => ({ id: p.id, name: p.name })),
          platforms: ["youtube", "instagram", "tiktok"],
        },
      },
      {
        onSuccess: (data) => {
          setSearchResults(data);
          // Surface any warnings the server returned (e.g. missing API keys)
          const warnings = (data as VideoSearchResult & { warnings?: string[] }).warnings ?? [];
          setSearchWarnings(warnings);
          if (warnings.length) {
            toast({
              title: "Search completed with warnings",
              description: warnings[0],
              variant: "destructive",
            });
          }
        },
        onError: () =>
          toast({ title: "Video search failed", variant: "destructive" }),
      }
    );
  };

  const handleDownload = (video: VideoResult) => {
    downloadVideoMutation.mutate(
      {
        data: {
          url: video.url,
          platform: video.platform,
          title: video.title,
          productId: video.productId,
        },
      },
      {
        onSuccess: (job) => {
          toast({ title: "Download started" });
          setActiveJobs((prev) => [...prev, job]);
        },
        onError: () =>
          toast({ title: "Failed to start download", variant: "destructive" }),
      }
    );
  };

  const handleJobComplete = useCallback((jobId: string) => {
    setActiveJobs((prev) => prev.filter((j) => j.jobId !== jobId));
  }, []);

  // Completed downloads from the server (excludes active jobs already in activeJobs)
  const completedDownloads: DownloadedFile[] = (downloadsData?.downloads ?? []).filter(
    (dl) => !activeJobs.some((aj) => aj.jobId === dl.jobId)
  );

  const hasDownloads = activeJobs.length > 0 || completedDownloads.length > 0;

  // ── Render ─────────────────────────────────────────────────────────────

  return (
    <div className="h-screen w-full flex flex-col bg-background text-foreground overflow-hidden">
      {/* Header */}
      <header className="h-14 border-b flex items-center px-4 shrink-0 bg-card">
        <FileVideo className="w-5 h-5 mr-2 text-primary" />
        <h1 className="font-semibold text-sm tracking-tight">
          Product Video Finder
        </h1>
      </header>

      {/* 3-panel layout */}
      <main className="flex-1 flex overflow-hidden">
        {/* ── Left Panel: Products spreadsheet table ── */}
        <section className="w-[480px] border-r flex flex-col bg-card/50 shrink-0">
          <div className="p-3 border-b flex items-center justify-between bg-card">
            <h2 className="font-medium text-sm">
              Products
              {selectedProducts.length > 0 && (
                <span className="ml-2 text-muted-foreground font-normal">
                  ({selectedProducts.length}/5 selected)
                </span>
              )}
            </h2>
            <Button
              size="sm"
              variant="outline"
              onClick={handleScrape}
              disabled={isLoadingProducts}
              className="h-8 px-2 text-xs"
            >
              <RefreshCw
                className={`w-3.5 h-3.5 mr-1.5 ${isLoadingProducts ? "animate-spin" : ""}`}
              />
              Refresh
            </Button>
          </div>

          {/* Spreadsheet-style table */}
          <div className="flex-1 overflow-auto">
            <table className="w-full text-xs border-collapse">
              <thead className="sticky top-0 z-10 bg-muted/80 backdrop-blur-sm">
                <tr className="border-b">
                  <th className="w-8 px-2 py-2 text-center font-medium text-muted-foreground"></th>
                  <th className="w-10 px-1 py-2 text-left font-medium text-muted-foreground">Img</th>
                  <th className="px-2 py-2 text-left font-medium text-muted-foreground">Name</th>
                  <th className="w-20 px-2 py-2 text-left font-medium text-muted-foreground">Price</th>
                  <th className="w-10 px-2 py-2 text-left font-medium text-muted-foreground">URL</th>
                </tr>
              </thead>
              <tbody>
                {isLoadingProducts
                  ? [1, 2, 3, 4, 5].map((i) => (
                      <tr key={i} className="border-b">
                        <td colSpan={5} className="px-2 py-2">
                          <Skeleton className="h-8 w-full" />
                        </td>
                      </tr>
                    ))
                  : productsData?.products?.length
                  ? productsData.products.map((p) => {
                      const isSelected = selectedProducts.some((sp) => sp.id === p.id);
                      return (
                        <tr
                          key={p.id}
                          className={`border-b hover:bg-muted/40 transition-colors cursor-pointer ${
                            isSelected ? "bg-primary/8" : ""
                          }`}
                          onClick={() => handleProductToggle(p, !isSelected)}
                        >
                          <td className="px-2 py-1.5 text-center">
                            <Checkbox
                              checked={isSelected}
                              onCheckedChange={(c) => handleProductToggle(p, !!c)}
                              onClick={(e) => e.stopPropagation()}
                            />
                          </td>
                          <td className="px-1 py-1.5">
                            {p.imageUrl ? (
                              <img
                                src={p.imageUrl}
                                alt={p.name}
                                className="w-8 h-8 object-cover rounded border"
                              />
                            ) : (
                              <div className="w-8 h-8 rounded border bg-muted flex items-center justify-center">
                                <FileVideo className="w-3 h-3 text-muted-foreground" />
                              </div>
                            )}
                          </td>
                          <td className="px-2 py-1.5 font-medium leading-tight">
                            <span className="line-clamp-2" title={p.name}>{p.name}</span>
                          </td>
                          <td className="px-2 py-1.5 text-muted-foreground whitespace-nowrap">
                            {p.price || "—"}
                          </td>
                          <td className="px-2 py-1.5">
                            <a
                              href={p.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              onClick={(e) => e.stopPropagation()}
                              className="text-primary underline underline-offset-2 hover:opacity-75"
                              title={p.url}
                            >
                              View
                            </a>
                          </td>
                        </tr>
                      );
                    })
                  : (
                    <tr>
                      <td colSpan={5} className="px-4 py-8 text-center text-muted-foreground">
                        <AlertCircle className="w-6 h-6 mx-auto mb-2 opacity-20" />
                        <p>No products found</p>
                        <Button size="sm" variant="outline" onClick={handleScrape} className="mt-2 h-7 text-xs px-3">
                          <RefreshCw className="w-3 h-3 mr-1.5" /> Retry
                        </Button>
                      </td>
                    </tr>
                  )}
              </tbody>
            </table>
          </div>

          {/* Pagination */}
          <div className="p-3 border-t bg-card flex items-center justify-between shrink-0">
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              <ChevronLeft className="w-4 h-4" />
            </Button>
            <span className="text-xs text-muted-foreground">
              Page {page} of {productsData?.totalPages || 1}
            </span>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              disabled={page >= (productsData?.totalPages || 1)}
              onClick={() => setPage((p) => p + 1)}
            >
              <ChevronRight className="w-4 h-4" />
            </Button>
          </div>
        </section>

        {/* ── Center Panel: Video Search ── */}
        <section className="flex-1 flex flex-col min-w-0 bg-background">
          <div className="p-3 border-b flex items-center justify-between bg-card shrink-0">
            <h2 className="font-medium text-sm flex items-center">
              Video Search
              {selectedProducts.length > 0 && (
                <Badge variant="secondary" className="ml-2 h-5 text-[10px]">
                  {selectedProducts.length}/5 Selected
                </Badge>
              )}
            </h2>
            <Button
              size="sm"
              onClick={handleSearch}
              disabled={
                selectedProducts.length === 0 || searchVideosMutation.isPending
              }
              className="h-8 px-3 text-xs"
            >
              <Search
                className={`w-3.5 h-3.5 mr-1.5 ${
                  searchVideosMutation.isPending ? "animate-spin" : ""
                }`}
              />
              Search Videos
            </Button>
          </div>

          {/* Warning banner for missing API keys */}
          <WarningBanner warnings={searchWarnings} />

          <ScrollArea className="flex-1 p-4">
            {searchVideosMutation.isPending ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {[1, 2, 3, 4].map((i) => (
                  <Skeleton key={i} className="h-64 w-full" />
                ))}
              </div>
            ) : searchResults?.results?.length ? (
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                {searchResults.results.map((video: VideoResult) => (
                  <Card key={video.id} className="overflow-hidden flex flex-col">
                    {video.embedUrl ? (
                      <div className="aspect-video bg-black relative">
                        <iframe
                          src={video.embedUrl}
                          className="absolute inset-0 w-full h-full"
                          allowFullScreen
                          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                        />
                      </div>
                    ) : (
                      <VideoPreview video={video} />
                    )}
                    <CardContent className="p-4 flex-1 flex flex-col">
                      <div className="flex items-start justify-between gap-2 mb-2">
                        <h3 className="font-medium text-sm line-clamp-2 leading-tight">
                          {video.title}
                        </h3>
                        <Badge
                          variant="outline"
                          className={`text-[10px] uppercase shrink-0 ${
                            platformColors[video.platform?.toLowerCase()] || ""
                          }`}
                        >
                          {video.platform}
                        </Badge>
                      </div>

                      <div className="text-xs text-muted-foreground mb-4 space-y-1">
                        {video.channelName && (
                          <p className="truncate">
                            Channel: {video.channelName}
                          </p>
                        )}
                        <div className="flex gap-3">
                          {video.viewCount && (
                            <span>
                              {video.viewCount.toLocaleString()} views
                            </span>
                          )}
                          {video.duration && <span>{video.duration}</span>}
                        </div>
                      </div>

                      <div className="mt-auto pt-2 flex items-center justify-between border-t border-border/50">
                        <span
                          className="text-[10px] text-muted-foreground truncate max-w-[150px]"
                          title={video.productName}
                        >
                          For: {video.productName}
                        </span>
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => handleDownload(video)}
                          className="h-7 text-xs px-2"
                        >
                          <Download className="w-3 h-3 mr-1.5" /> Download
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            ) : searchResults ? (
              <div className="p-8 text-center text-muted-foreground">
                <p className="text-sm">
                  No videos found for selected products.
                </p>
              </div>
            ) : (
              <div className="h-full flex flex-col items-center justify-center text-muted-foreground opacity-50 min-h-[300px]">
                <Search className="w-12 h-12 mb-4" />
                <p className="text-sm">
                  Select products and search to find videos.
                </p>
              </div>
            )}
          </ScrollArea>
        </section>

        {/* ── Right Panel: Downloads ── */}
        <section className="w-[320px] border-l flex flex-col bg-card/50">
          <div className="p-3 border-b flex items-center justify-between bg-card">
            <h2 className="font-medium text-sm">Downloads</h2>
            {hasDownloads && (
              <Badge variant="secondary" className="text-[10px] h-5">
                {activeJobs.length + completedDownloads.length}
              </Badge>
            )}
          </div>

          <ScrollArea className="flex-1 p-3">
            <div className="space-y-3">
              {activeJobs.map((job) => (
                <DownloadItem
                  key={job.jobId}
                  job={job}
                  onComplete={handleJobComplete}
                />
              ))}

              {completedDownloads.map((dl) => (
                <DownloadItem key={dl.jobId} job={dl} />
              ))}

              {!hasDownloads && (
                <div className="text-center py-8 text-muted-foreground">
                  <Download className="w-8 h-8 mx-auto mb-2 opacity-20" />
                  <p className="text-xs">No downloads yet.</p>
                </div>
              )}
            </div>
          </ScrollArea>
        </section>
      </main>
    </div>
  );
}
