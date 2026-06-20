import { Router, type IRouter } from "express";
import { scrapeProducts, getPaginatedProducts } from "../../lib/productScraper";
import { ScrapeProductsQueryParams } from "@workspace/api-zod";

const router: IRouter = Router();

router.get("/products", async (req, res): Promise<void> => {
  const parsed = ScrapeProductsQueryParams.safeParse(req.query);
  const page = parsed.success ? (parsed.data.page ?? 1) : 1;
  const refresh = parsed.success ? (parsed.data.refresh ?? false) : false;
  const search = typeof req.query.search === "string" ? req.query.search : undefined;

  try {
    const allProducts = await scrapeProducts(refresh);
    const { products, total, totalPages } = getPaginatedProducts(allProducts, page, search);
    res.json({ products, total, page, totalPages });
  } catch (err: any) {
    req.log.error({ err }, "Failed to scrape products");
    res.status(500).json({ error: "Failed to scrape products. Please try again." });
  }
});

export default router;
