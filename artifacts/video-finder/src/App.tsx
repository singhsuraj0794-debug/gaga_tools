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
import MonitoringDashboard from "@/pages/MonitoringDashboard";
import TrendFinder from "@/pages/TrendFinder";
import PreListingValidator from "@/pages/PreListingValidator";
import CategoryValidator from "@/pages/CategoryValidator";
import ProductList from "@/pages/product-enhance/ProductList";
import ProductDetail from "@/pages/product-enhance/ProductDetail";
import VoiceAgentDashboard from "@/pages/VoiceAgentDashboard";
import { setBaseUrl } from "@workspace/api-client-react";
import { API_BASE } from "@/lib/api";

// The generated API client is used by the Dashboard/Scraper pages. It used to be
// pointed at the Render deployment, which no longer serves the scrapers (jobs are
// kept in memory there, and that host is not where the browsers live), so every
// call from those pages failed to connect. Use the same base as the rest of the
// app - the always-on Windows box.
setBaseUrl(API_BASE);

const queryClient = new QueryClient();

function Router() {
  return (
    <Switch>
      <Route path="/" component={Home} />
      <Route path="/video-finder" component={Dashboard} />
      <Route path="/trend-finder" component={TrendFinder} />
      <Route path="/meesho-scraper" component={MeeshoScraper} />
      <Route path="/price-mapper" component={PriceMapper} />
      <Route path="/flipkart-scraper" component={FlipkartScraper} />
      <Route path="/amazon-scraper" component={AmazonScraper} />
      <Route path="/monitoring" component={MonitoringDashboard} />
      <Route path="/pre-listing-validator" component={PreListingValidator} />
      <Route path="/rd/category-validation" component={CategoryValidator} />
      <Route path="/rd/product-enhance" component={ProductList} />
      <Route path="/rd/product-enhance/list" component={ProductList} />
      <Route path="/rd/product-enhance/:id" component={ProductDetail} />
      <Route path="/Rnd/voice-agent/dashboard" component={VoiceAgentDashboard} />
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
