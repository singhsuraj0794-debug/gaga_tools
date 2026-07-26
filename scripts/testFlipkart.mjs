const url = "https://www.flipkart.com/kuber-industries-13-inch-round-beige-jute-placemat-set-4/p/itmfcbdfe7e7b66c";

console.log("Testing Flipkart scraper (stealth mode)...");
const start = Date.now();

const res = await fetch("http://localhost:8080/api/scraper/flipkart/scrape", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ urls: [url] }),
});
const data = await res.json();
console.log(`Done in ${((Date.now()-start)/1000).toFixed(1)}s`);
console.log("Products:", data.products?.length);
if (data.products?.[0]) {
  const p = data.products[0];
  console.log("Title:", p.title);
  console.log("Price:", p.price);
  console.log("HSN:", p.hsn);
  console.log("GST:", p.gst);
  console.log("Dimensions:", p.dimensions);
  console.log("Weight:", p.weight);
  console.log("Image:", p.imageUrl ? "✓" : "✗");
}
