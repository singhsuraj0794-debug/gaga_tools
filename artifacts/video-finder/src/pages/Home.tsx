import { Link } from "wouter";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Video,
  TrendingUp,
  ShoppingBag,
  ShoppingCart,
  Package,
} from "lucide-react";

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
  ];

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
      <div className="max-w-7xl mx-auto px-6 py-16">
        <div className="text-center mb-12">
          <h1 className="text-4xl font-bold text-slate-900 mb-4">Product Video Scraper</h1>
          <p className="text-lg text-slate-600 max-w-2xl mx-auto">
            Choose a tool below to get started with scraping products or finding videos
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
      </div>
    </div>
  );
}
