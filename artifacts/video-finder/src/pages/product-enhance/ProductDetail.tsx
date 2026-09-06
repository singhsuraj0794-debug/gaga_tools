import { useState } from "react";
import { Link, useParams } from "wouter";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ArrowLeft, Star, Share2, MapPin, Shield, Truck } from "lucide-react";
import { products } from "./products";
import VoiceBargainModal from "./VoiceBargainModal";

export default function ProductDetail() {
  const params = useParams();
  const product = products.find((p) => p.id === params.id);
  const [showBargainModal, setShowBargainModal] = useState(false);

  if (!product) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100 flex items-center justify-center">
        <div className="text-center">
          <h1 className="text-2xl font-bold text-slate-900 mb-2">Product not found</h1>
          <Link href="/rd/product-enhance/list" className="text-blue-600 hover:underline">
            Back to products
          </Link>
        </div>
      </div>
    );
  }

  const discount = Math.round(((product.mrp - product.price) / product.mrp) * 100);

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-slate-100">
      {/* Header */}
      <div className="bg-white border-b sticky top-0 z-40">
        <div className="max-w-7xl mx-auto px-4 py-3">
          <div className="flex items-center gap-4">
            <Link href="/rd/product-enhance/list" className="text-slate-600 hover:text-slate-900">
              <ArrowLeft className="h-5 w-5" />
            </Link>
            <h1 className="text-lg font-medium text-slate-900 truncate">{product.name}</h1>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 py-6">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          {/* Left - Image */}
          <div className="space-y-4">
            <Card className="overflow-hidden">
              <div className="aspect-square relative bg-white">
                  <img
                    src={product.image}
                    alt={product.name}
                    className="w-full h-full object-contain p-4"
                    onError={(e) => {
                      e.currentTarget.src = "https://placehold.co/600x600/f3f4f6/374151?text=No+Image";
                    }}
                  />
                <Badge className="absolute top-4 left-4 bg-green-500 text-white">
                  {discount}% OFF
                </Badge>
              </div>
            </Card>
          </div>

          {/* Right - Details */}
          <div className="space-y-6">
            {/* Breadcrumb */}
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Link href="/" className="hover:text-slate-900">Home</Link>
              <span>/</span>
              <span>{product.category}</span>
              <span>/</span>
              <span className="text-slate-900">{product.brand}</span>
            </div>

            {/* Title */}
            <h1 className="text-2xl font-bold text-slate-900">{product.name}</h1>

            {/* Rating & Share */}
            <div className="flex items-center gap-4">
              <div className="flex items-center gap-1">
                <Star className="h-4 w-4 fill-amber-400 text-amber-400" />
                <span className="font-medium">{product.rating}</span>
                <span className="text-slate-500">(120 reviews)</span>
              </div>
              <Button variant="ghost" size="sm">
                <Share2 className="h-4 w-4 mr-2" />
                Share
              </Button>
            </div>

            {/* Sold By */}
            <div className="flex items-center gap-3 p-3 bg-slate-50 rounded-lg">
              <div className="w-10 h-10 bg-blue-100 rounded-full flex items-center justify-center">
                <span className="text-blue-600 font-bold">{product.brand[0]}</span>
              </div>
              <div>
                <p className="text-sm font-medium text-slate-900">SOLD BY</p>
                <p className="text-sm text-slate-600">{product.brand} Official</p>
              </div>
              <Badge variant="outline" className="ml-auto text-green-600 border-green-300 bg-green-50">
                Verified
              </Badge>
            </div>

            {/* Price */}
            <div className="space-y-2">
              <div className="flex items-baseline gap-3">
                <span className="text-3xl font-bold text-slate-900">₹{product.price}</span>
                <span className="text-lg text-slate-500 line-through">₹{product.mrp}</span>
                <Badge className="bg-green-500">Save ₹{product.mrp - product.price}</Badge>
              </div>
              <p className="text-sm text-slate-500">Inclusive of all taxes</p>
            </div>

            {/* Delivery */}
            <div className="flex items-center gap-3 p-3 border rounded-lg">
              <MapPin className="h-5 w-5 text-slate-600" />
              <div className="flex-1">
                <p className="text-sm font-medium">Delivery</p>
                <p className="text-sm text-slate-500">Enter pincode for delivery date</p>
              </div>
              <Button variant="outline" size="sm">Check</Button>
            </div>

            {/* Features */}
            <div className="space-y-3">
              <h3 className="font-semibold text-slate-900">Key Features</h3>
              <ul className="space-y-2">
                {product.features.map((feature, i) => (
                  <li key={i} className="flex items-start gap-2 text-sm text-slate-600">
                    <Shield className="h-4 w-4 text-green-500 mt-0.5 shrink-0" />
                    {feature}
                  </li>
                ))}
              </ul>
            </div>

            {/* Bargain Button */}
            <Button
              className="w-full h-14 text-lg font-semibold bg-gradient-to-r from-orange-500 to-amber-500 hover:from-orange-600 hover:to-amber-600"
              onClick={() => setShowBargainModal(true)}
            >
              <Truck className="h-5 w-5 mr-2" />
              Start Bargaining
            </Button>

            {/* Info Badges */}
            <div className="grid grid-cols-3 gap-3">
              <div className="text-center p-3 bg-slate-50 rounded-lg">
                <Truck className="h-6 w-6 mx-auto mb-1 text-slate-600" />
                <p className="text-xs font-medium">Free Delivery</p>
              </div>
              <div className="text-center p-3 bg-slate-50 rounded-lg">
                <Shield className="h-6 w-6 mx-auto mb-1 text-slate-600" />
                <p className="text-xs font-medium">Genuine Product</p>
              </div>
              <div className="text-center p-3 bg-slate-50 rounded-lg">
                <span className="text-2xl">💰</span>
                <p className="text-xs font-medium">Best Price</p>
              </div>
            </div>
          </div>
        </div>

        {/* Tabs Section */}
        <div className="mt-12">
          <Tabs defaultValue="description" className="w-full">
            <TabsList className="grid w-full grid-cols-4">
              <TabsTrigger value="description">Description</TabsTrigger>
              <TabsTrigger value="features">Features</TabsTrigger>
              <TabsTrigger value="specifications">Specifications</TabsTrigger>
              <TabsTrigger value="reviews">Reviews</TabsTrigger>
            </TabsList>
            <TabsContent value="description" className="mt-6">
              <Card>
                <CardContent className="p-6">
                  <h3 className="text-lg font-semibold mb-4">About this item</h3>
                  <p className="text-slate-600 leading-relaxed">{product.description}</p>
                </CardContent>
              </Card>
            </TabsContent>
            <TabsContent value="features" className="mt-6">
              <Card>
                <CardContent className="p-6">
                  <h3 className="text-lg font-semibold mb-4">Key Features</h3>
                  <ul className="space-y-3">
                    {product.features.map((feature, i) => (
                      <li key={i} className="flex items-center gap-3 text-slate-600">
                        <div className="w-2 h-2 bg-green-500 rounded-full" />
                        {feature}
                      </li>
                    ))}
                  </ul>
                </CardContent>
              </Card>
            </TabsContent>
            <TabsContent value="specifications" className="mt-6">
              <Card>
                <CardContent className="p-6">
                  <h3 className="text-lg font-semibold mb-4">Product Specifications</h3>
                  <div className="grid grid-cols-2 gap-4">
                    {Object.entries(product.specifications).map(([key, value]) => (
                      <div key={key} className="flex justify-between py-2 border-b">
                        <span className="text-slate-500">{key}</span>
                        <span className="font-medium text-slate-900">{value}</span>
                      </div>
                    ))}
                  </div>
                </CardContent>
              </Card>
            </TabsContent>
            <TabsContent value="reviews" className="mt-6">
              <Card>
                <CardContent className="p-6">
                  <h3 className="text-lg font-semibold mb-4">Customer Reviews</h3>
                  <div className="space-y-4">
                    <div className="p-4 bg-slate-50 rounded-lg">
                      <div className="flex items-center gap-2 mb-2">
                        <Star className="h-4 w-4 fill-amber-400 text-amber-400" />
                        <span className="font-medium">Great product!</span>
                      </div>
                      <p className="text-sm text-slate-600">Excellent quality and fast delivery. Highly recommended!</p>
                    </div>
                    <div className="p-4 bg-slate-50 rounded-lg">
                      <div className="flex items-center gap-2 mb-2">
                        <Star className="h-4 w-4 fill-amber-400 text-amber-400" />
                        <span className="font-medium">Value for money</span>
                      </div>
                      <p className="text-sm text-slate-600">Good product at this price range. Works as expected.</p>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </TabsContent>
          </Tabs>
        </div>
      </div>

      {/* Voice Bargain Modal */}
      <VoiceBargainModal
        open={showBargainModal}
        onClose={() => setShowBargainModal(false)}
        product={product}
      />
    </div>
  );
}
