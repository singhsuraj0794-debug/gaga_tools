import { Switch, Route, Router as WouterRouter } from "wouter";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { TooltipProvider } from "@/components/ui/tooltip";
import NotFound from "@/pages/not-found";
import Home from "@/pages/Home";
import Dashboard from "@/components/Dashboard";
import FlipkartScraper from "@/pages/FlipkartScraper";
import MeeshoScraper from "@/pages/MeeshoScraper";
import AmazonScraper from "@/pages/AmazonScraper";
import PriceMapper from "@/pages/PriceMapper";
import { setBaseUrl } from "@workspace/api-client-react";

setBaseUrl("https://product-video-scraper-api.onrender.com");

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
      <Route path="/meesho-scraper" component={MeeshoScraper} />
      <Route path="/price-mapper" component={PriceMapper} />
      <Route path="/flipkart-scraper" component={FlipkartScraper} />
      <Route path="/amazon-scraper" component={AmazonScraper} />
      <Route component={NotFound} />
    </Switch>
  );
}

function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
