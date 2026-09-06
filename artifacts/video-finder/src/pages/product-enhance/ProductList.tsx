import { Link } from "wouter";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Star, ArrowLeft } from "lucide-react";
import { products } from "./products";

export default function ProductList() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
      <div className="max-w-7xl mx-auto px-6 py-12">
        {/* Header */}
        <div className="mb-8">
          <Link href="/" className="inline-flex items-center gap-2 text-slate-600 hover:text-slate-900 mb-4">
            <ArrowLeft className="h-4 w-4" />
            Back to Home
          </Link>
          <h1 className="text-3xl font-bold text-slate-900 mb-2">Product Enhancement</h1>
          <p className="text-slate-600">AI-powered product optimization & improvement suggestions</p>
        </div>

        {/* Product Grid */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
          {products.map((product) => (
            <Link key={product.id} href={`/rd/product-enhance/${product.id}`}>
              <Card className="cursor-pointer transition-all duration-300 hover:shadow-lg hover:-translate-y-1 h-full">
                <div className="aspect-square relative overflow-hidden rounded-t-xl bg-slate-100">
                  <img
                    src={product.image}
                    alt={product.name}
                    className="w-full h-full object-cover"
                    onError={(e) => {
                      e.currentTarget.src = "https://placehold.co/400x400/f3f4f6/374151?text=No+Image";
                    }}
                  />
                  <Badge className="absolute top-2 left-2 bg-green-500">
                    {Math.round(((product.mrp - product.price) / product.mrp) * 100)}% OFF
                  </Badge>
                </div>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-medium line-clamp-2 text-slate-800">
                    {product.name}
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  <div className="flex items-center gap-1 mb-2">
                    <Star className="h-3 w-3 fill-amber-400 text-amber-400" />
                    <span className="text-xs text-slate-600">{product.rating}</span>
                    <Badge variant="outline" className="ml-auto text-xs">
                      {product.brand}
                    </Badge>
                  </div>
                  <div className="flex items-baseline gap-2">
                    <span className="text-lg font-bold text-slate-900">₹{product.price}</span>
                    <span className="text-sm text-slate-500 line-through">₹{product.mrp}</span>
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}
