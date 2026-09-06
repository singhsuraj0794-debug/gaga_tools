import { Link } from "wouter";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Video,
  TrendingUp,
  ShoppingBag,
  ShoppingCart,
  Package,
  BadgePercent,
  Activity,
  ClipboardCheck,
  FlaskConical,
  Sparkles,
  Zap,
  Lightbulb,
  Wand2,
} from "lucide-react";
import ProductSync from "@/components/ProductSync";

export default function Home() {
  const sections = [
    {
      title: "Product Video Finder",
      description: "Find and download product videos from various platforms",
      icon: <Video className="h-10 w-10 text-blue-600" />,
      path: "/video-finder",
      color: "bg-blue-50 hover:bg-blue-100 border-blue-200",
    },
    {
      title: "Trend Finder",
      description: "Discover trending products in the market",
      icon: <TrendingUp className="h-10 w-10 text-purple-600" />,
      path: "/trend-finder",
      color: "bg-purple-50 hover:bg-purple-100 border-purple-200",
    },
    {
      title: "Price Mapper",
      description: "Compare Gajab.com product prices across platforms",
      icon: <BadgePercent className="h-10 w-10 text-indigo-600" />,
      path: "/price-mapper",
      color: "bg-indigo-50 hover:bg-indigo-100 border-indigo-200",
    },
    {
      title: "Meesho Scraper",
      description: "Scrape products from Meesho",
      icon: <ShoppingBag className="h-10 w-10 text-orange-600" />,
      path: "/meesho-scraper",
      color: "bg-orange-50 hover:bg-orange-100 border-orange-200",
    },
    {
      title: "Flipkart Scraper",
      description: "Scrape products from Flipkart",
      icon: <ShoppingCart className="h-10 w-10 text-green-600" />,
      path: "/flipkart-scraper",
      color: "bg-green-50 hover:bg-green-100 border-green-200",
    },
    {
      title: "Amazon Scraper",
      description: "Scrape products from Amazon",
      icon: <Package className="h-10 w-10 text-yellow-600" />,
      path: "/amazon-scraper",
      color: "bg-yellow-50 hover:bg-yellow-100 border-yellow-200",
    },
    {
      title: "Synthetic Monitor",
      description: "Gajab.com happy flow — hourly Playwright checks with Slack & SMS alerts",
      icon: <Activity className="h-10 w-10 text-rose-600" />,
      path: "/monitoring",
      color: "bg-rose-50 hover:bg-rose-100 border-rose-200",
    },
    {
      title: "Pre-Listing Validator",
      description: "Validate Gajab Hub exports — HSN checks, duplicates, images, compliance",
      icon: <ClipboardCheck className="h-10 w-10 text-teal-600" />,
      path: "/pre-listing-validator",
      color: "bg-teal-50 hover:bg-teal-100 border-teal-200",
    },
  ];

  const rdSections = [
    {
      title: "AI Product Descriptions",
      description: "Generate compelling product descriptions using AI",
      icon: <Sparkles className="h-8 w-8 text-violet-600" />,
      path: "/rd/ai-descriptions",
      status: "Beta",
    },
    {
      title: "Smart Price Optimizer",
      description: "ML-powered pricing suggestions based on market data",
      icon: <Zap className="h-8 w-8 text-amber-600" />,
      path: "/rd/price-optimizer",
      status: "Testing",
    },
    {
      title: "Image Enhancement",
      description: "Auto-enhance product images for better conversions",
      icon: <Lightbulb className="h-8 w-8 text-emerald-600" />,
      path: "/rd/image-enhance",
      status: "Prototype",
    },
    {
      title: "Product Enhancement",
      description: "AI-powered product optimization & improvement suggestions",
      icon: <Wand2 className="h-8 w-8 text-cyan-600" />,
      path: "/rd/product-enhance/list",
      status: "Idea",
    },
  ];

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
      <div className="max-w-7xl mx-auto px-6 py-16">
        <div className="text-center mb-12">
           <h1 className="text-4xl font-bold text-slate-900 mb-4">Gajab Hub</h1>
          <p className="text-lg text-slate-600 max-w-2xl mx-auto">
            Choose a tool below to get started with your workflow.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
          {sections.map((section) => (
            <Link key={section.title} href={section.path}>
              <Card
                className={`cursor-pointer transition-all duration-300 border-2 ${section.color} hover:shadow-lg hover:-translate-y-1`}
              >
                <CardHeader>
                  <div className="flex items-center gap-4">
                    {section.icon}
                    <CardTitle className="text-xl font-semibold">
                      {section.title}
                    </CardTitle>
                  </div>
                </CardHeader>
                <CardContent>
                  <p className="text-slate-600">{section.description}</p>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>

        {/* R&D Section */}
        <div className="mt-16">
          <div className="flex items-center gap-3 mb-8">
            <div className="p-2 bg-gradient-to-br from-violet-500 to-purple-600 rounded-lg">
              <FlaskConical className="h-6 w-6 text-white" />
            </div>
            <div>
              <h2 className="text-2xl font-bold text-slate-900 flex items-center gap-2">
                R&D Lab
                <Badge variant="outline" className="bg-amber-100 text-amber-700 border-amber-300 text-xs">
                  Experimental
                </Badge>
              </h2>
              <p className="text-slate-500 text-sm">Features in development & testing</p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {rdSections.map((section) => (
              <Link key={section.title} href={section.path}>
                <Card className="cursor-pointer transition-all duration-300 border-2 border-dashed border-violet-200 bg-gradient-to-br from-violet-50 to-purple-50 hover:from-violet-100 hover:to-purple-100 hover:shadow-lg hover:-translate-y-1 hover:border-violet-300">
                  <CardHeader>
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        {section.icon}
                        <CardTitle className="text-lg font-semibold text-slate-800">
                          {section.title}
                        </CardTitle>
                      </div>
                      <Badge variant="secondary" className="bg-violet-100 text-violet-700 text-xs">
                        {section.status}
                      </Badge>
                    </div>
                  </CardHeader>
                  <CardContent>
                    <p className="text-slate-600 text-sm">{section.description}</p>
                  </CardContent>
                </Card>
              </Link>
            ))}
          </div>
        </div>

        <div className="mt-12">
          <ProductSync />
        </div>
      </div>
    </div>
  );
}
