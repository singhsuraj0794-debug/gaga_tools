import React, { useState, useEffect } from "react";
import { 
  useScrapeProducts, 
  useSearchVideos, 
  useDownloadVideo, 
  useListDownloads, 
  useGetDownloadStatus,
  getListDownloadsQueryKey,
  getGetDownloadStatusQueryKey
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Skeleton } from "@/components/ui/skeleton";
import { Progress } from "@/components/ui/progress";
import { Play, Download, Search, RefreshCw, AlertCircle, FileVideo, ChevronRight, ChevronLeft } from "lucide-react";
import { useToast } from "@/hooks/use-toast";

// --- Sub-components ---

function DownloadItem({ job }: { job: any }) {
  const { data: statusData } = useGetDownloadStatus(job.jobId, {
    query: {
      enabled: job.status === "pending" || job.status === "downloading",
      refetchInterval: (query) => {
        const state = query.state.data?.status;
        return state === "pending" || state === "downloading" ? 2000 : false;
      },
      queryKey: getGetDownloadStatusQueryKey(job.jobId)
    }
  });

  const displayData = statusData || job;
  const isCompleted = displayData.status === "completed";
  const isDownloading = displayData.status === "downloading";
  const progress = displayData.progress || 0;

  return (
    <div className="flex flex-col gap-2 p-3 border rounded-md bg-card shadow-sm text-sm">
      <div className="flex justify-between items-start gap-2">
        <div className="font-medium truncate" title={displayData.title || displayData.fileName || "Video"}>
          {displayData.title || displayData.fileName || "Video"}
        </div>
        <Badge variant="outline" className="text-[10px] shrink-0 uppercase">
          {displayData.platform || "unknown"}
        </Badge>
      </div>
      
      <div className="flex items-center justify-between mt-1 text-muted-foreground text-xs">
        <span className="capitalize">{displayData.status}</span>
        {displayData.fileSize && <span>{(displayData.fileSize / (1024 * 1024)).toFixed(1)} MB</span>}
      </div>

      {isDownloading && (
        <Progress value={progress} className="h-1.5 mt-1" />
      )}
      
      {isCompleted && displayData.filePath && (
        <Button variant="secondary" size="sm" className="w-full mt-2 h-7 text-xs" asChild>
          <a href={`/api/videos/downloads/${displayData.jobId}/play`} target="_blank" rel="noopener noreferrer">
            <Play className="w-3 h-3 mr-1.5" /> Play
          </a>
        </Button>
      )}
    </div>
  );
}

export default function Dashboard() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  
  // State
  const [page, setPage] = useState(1);
  const [selectedProducts, setSelectedProducts] = useState<any[]>([]);
  const [forceRefresh, setForceRefresh] = useState(false);

  // Queries & Mutations
  const { data: productsData, isLoading: isLoadingProducts } = useScrapeProducts(
    { page, refresh: forceRefresh || undefined },
    { query: { staleTime: 0 } }
  );
  
  const searchVideos = useSearchVideos();
  const downloadVideo = useDownloadVideo();
  
  const { data: downloadsData } = useListDownloads();

  const [searchResults, setSearchResults] = useState<any>(null);

  // Reset forceRefresh once data arrives
  useEffect(() => {
    if (forceRefresh && productsData) {
      setForceRefresh(false);
    }
  }, [productsData, forceRefresh]);

  // Handlers
  const handleScrape = () => {
    setForceRefresh(true);
  };

  const handleProductToggle = (product: any, checked: boolean) => {
    if (checked) {
      if (selectedProducts.length >= 5) {
        toast({ title: "Maximum 5 products selected", variant: "destructive" });
        return;
      }
      setSelectedProducts(prev => [...prev, product]);
    } else {
      setSelectedProducts(prev => prev.filter(p => p.id !== product.id));
    }
  };

  const handleSearch = () => {
    if (selectedProducts.length === 0) return;
    searchVideos.mutate({
      data: {
        products: selectedProducts.map(p => ({ id: p.id, name: p.name }))
      }
    }, {
      onSuccess: (data) => setSearchResults(data)
    });
  };

  const handleDownload = (video: any) => {
    downloadVideo.mutate({
      data: {
        url: video.url,
        platform: video.platform,
        title: video.title,
        productId: video.productId
      }
    }, {
      onSuccess: () => {
        toast({ title: "Download started" });
        queryClient.invalidateQueries({ queryKey: getListDownloadsQueryKey() });
      }
    });
  };

  const platformColors: Record<string, string> = {
    youtube: "bg-red-500/10 text-red-600 border-red-500/20",
    instagram: "bg-fuchsia-500/10 text-fuchsia-600 border-fuchsia-500/20",
    facebook: "bg-blue-600/10 text-blue-600 border-blue-600/20",
    tiktok: "bg-slate-800/10 text-slate-800 border-slate-800/20 dark:bg-white/10 dark:text-white dark:border-white/20",
  };

  return (
    <div className="h-screen w-full flex flex-col bg-background text-foreground overflow-hidden">
      {/* Header */}
      <header className="h-14 border-b flex items-center px-4 shrink-0 bg-card">
        <FileVideo className="w-5 h-5 mr-2 text-primary" />
        <h1 className="font-semibold text-sm tracking-tight">Product Video Finder</h1>
      </header>

      {/* Main 3-panel layout */}
      <main className="flex-1 flex overflow-hidden">
        
        {/* Left Panel: Products */}
        <section className="w-[400px] border-r flex flex-col bg-card/50">
          <div className="p-3 border-b flex items-center justify-between bg-card">
            <h2 className="font-medium text-sm">Products</h2>
            <Button size="sm" variant="outline" onClick={handleScrape} disabled={isLoadingProducts} className="h-8 px-2 text-xs">
              <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${isLoadingProducts ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
          </div>
          
          <ScrollArea className="flex-1">
            {isLoadingProducts ? (
              <div className="p-4 space-y-3">
                {[1,2,3,4].map(i => <Skeleton key={i} className="h-16 w-full" />)}
              </div>
            ) : productsData?.products?.length ? (
              <div className="divide-y">
                {productsData.products.map((p) => {
                  const isSelected = selectedProducts.some(sp => sp.id === p.id);
                  return (
                    <div key={p.id} className={`flex items-start gap-3 p-3 hover:bg-muted/50 transition-colors ${isSelected ? 'bg-primary/5' : ''}`}>
                      <Checkbox 
                        checked={isSelected}
                        onCheckedChange={(c) => handleProductToggle(p, !!c)}
                        className="mt-1"
                      />
                      {p.imageUrl ? (
                        <div className="w-12 h-12 shrink-0 rounded overflow-hidden bg-muted border">
                          <img src={p.imageUrl} alt={p.name} className="w-full h-full object-cover" />
                        </div>
                      ) : (
                        <div className="w-12 h-12 shrink-0 rounded bg-muted border flex items-center justify-center">
                           <FileVideo className="w-4 h-4 text-muted-foreground" />
                        </div>
                      )}
                      <div className="flex-1 min-w-0">
                        <h3 className="text-sm font-medium leading-tight truncate">{p.name}</h3>
                        <div className="flex items-center gap-2 mt-1 text-xs text-muted-foreground">
                          {p.price && <span>{p.price}</span>}
                          {p.category && <span className="truncate">{p.category}</span>}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div className="p-8 text-center text-muted-foreground flex flex-col items-center gap-3">
                <AlertCircle className="w-8 h-8 opacity-20" />
                <p className="text-sm">No products found</p>
                <Button size="sm" variant="outline" onClick={handleScrape} className="h-7 text-xs px-3">
                  <RefreshCw className="w-3 h-3 mr-1.5" /> Retry
                </Button>
              </div>
            )}
          </ScrollArea>
          
          {/* Pagination */}
          <div className="p-3 border-t bg-card flex items-center justify-between">
            <Button variant="ghost" size="icon" className="h-7 w-7" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>
              <ChevronLeft className="w-4 h-4" />
            </Button>
            <span className="text-xs text-muted-foreground">Page {page} of {productsData?.totalPages || 1}</span>
            <Button variant="ghost" size="icon" className="h-7 w-7" disabled={page >= (productsData?.totalPages || 1)} onClick={() => setPage(p => p + 1)}>
              <ChevronRight className="w-4 h-4" />
            </Button>
          </div>
        </section>

        {/* Center Panel: Video Search */}
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
              disabled={selectedProducts.length === 0 || searchVideos.isPending}
              className="h-8 px-3 text-xs"
            >
              <Search className={`w-3.5 h-3.5 mr-1.5 ${searchVideos.isPending ? 'animate-spin' : ''}`} />
              Search Videos
            </Button>
          </div>

          <ScrollArea className="flex-1 p-4">
            {searchVideos.isPending ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {[1,2,3,4].map(i => <Skeleton key={i} className="h-64 w-full" />)}
              </div>
            ) : searchResults?.results?.length ? (
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-4">
                {searchResults.results.map((video: any) => (
                  <Card key={video.id} className="overflow-hidden flex flex-col">
                    {video.embedUrl ? (
                      <div className="aspect-video bg-black relative">
                        <iframe 
                          src={video.embedUrl} 
                          className="absolute inset-0 w-full h-full"
                          allowFullScreen
                        />
                      </div>
                    ) : video.thumbnailUrl ? (
                      <div className="aspect-video bg-muted relative border-b">
                        <img src={video.thumbnailUrl} alt={video.title} className="w-full h-full object-cover" />
                        <div className="absolute inset-0 bg-black/20 flex items-center justify-center opacity-0 hover:opacity-100 transition-opacity">
                          <Button variant="secondary" size="icon" className="rounded-full w-12 h-12">
                            <Play className="w-5 h-5 ml-1" />
                          </Button>
                        </div>
                      </div>
                    ) : (
                       <div className="aspect-video bg-muted flex items-center justify-center border-b">
                         <FileVideo className="w-8 h-8 text-muted-foreground/30" />
                       </div>
                    )}
                    <CardContent className="p-4 flex-1 flex flex-col">
                      <div className="flex items-start justify-between gap-2 mb-2">
                        <h3 className="font-medium text-sm line-clamp-2 leading-tight">{video.title}</h3>
                        <Badge variant="outline" className={`text-[10px] uppercase shrink-0 ${platformColors[video.platform?.toLowerCase()] || ''}`}>
                          {video.platform}
                        </Badge>
                      </div>
                      
                      <div className="text-xs text-muted-foreground mb-4 space-y-1">
                        {video.channelName && <p className="truncate">Channel: {video.channelName}</p>}
                        <div className="flex gap-3">
                          {video.viewCount && <span>{video.viewCount.toLocaleString()} views</span>}
                          {video.duration && <span>{video.duration}</span>}
                        </div>
                      </div>

                      <div className="mt-auto pt-2 flex items-center justify-between border-t border-border/50">
                        <span className="text-[10px] text-muted-foreground truncate max-w-[150px]" title={video.productName}>
                          For: {video.productName}
                        </span>
                        <Button size="sm" variant="secondary" onClick={() => handleDownload(video)} className="h-7 text-xs px-2">
                          <Download className="w-3 h-3 mr-1.5" /> Download
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            ) : searchResults ? (
              <div className="p-8 text-center text-muted-foreground">
                <p className="text-sm">No videos found for selected products.</p>
              </div>
            ) : (
              <div className="h-full flex flex-col items-center justify-center text-muted-foreground opacity-50 min-h-[300px]">
                <Search className="w-12 h-12 mb-4" />
                <p className="text-sm">Select products and search to find videos.</p>
              </div>
            )}
          </ScrollArea>
        </section>

        {/* Right Panel: Downloads */}
        <section className="w-[320px] border-l flex flex-col bg-card/50">
          <div className="p-3 border-b flex items-center justify-between bg-card">
            <h2 className="font-medium text-sm">Downloads</h2>
          </div>
          
          <ScrollArea className="flex-1 p-3">
            <div className="space-y-3">
              {downloadsData?.downloads?.length ? (
                downloadsData.downloads.map((dl: any) => (
                  <DownloadItem key={dl.jobId} job={dl} />
                ))
              ) : (
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
