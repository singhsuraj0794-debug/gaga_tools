import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/not-found";
import Home from "@/pages/Home";
import Dashboard from "@/components/Dashboard";
import Scraper from "@/pages/Scraper";
import FlipkartScraper from "@/pages/FlipkartScraper";

const queryClient = new QueryClient();

function Router() {
  return (
    <Switch>
      <Route path="/" component={Home} />
      <Route path="/video-finder" component={Dashboard} />
      <Route path="/trend-finder">
        <div className="min-h-screen bg-gradient-to-br from-purple-50 to-purple-100 p-6">
          <div className="max-w-4xl mx-auto">
            <h1 className="text-3xl font-bold mb-4">Trend Finder</h1>
            <p className="text-slate-600">Coming soon!</p>
          </div>
        </div>
      </Route>
      <Route path="/meesho-scraper">
        <Scraper initialPlatform="meesho" />
      </Route>
      <Route path="/flipkart-scraper" component={FlipkartScraper} />
      <Route path="/amazon-scraper">
        <Scraper initialPlatform="amazon" />
      </Route>
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, "")}>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
