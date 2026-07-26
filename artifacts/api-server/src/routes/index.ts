import { Router, type IRouter } from "express";
import healthRouter from "./health";
import productsRouter from "./products";
import videosRouter from "./videos";
import scraperRouter from "./scraper";
import meeshoScraperRouter from "./scraper/meesho";
import amazonScraperRouter from "./scraper/amazon";
import priceMapperRouter from "./priceMapper";

const router: IRouter = Router();

router.use(healthRouter);
router.use(productsRouter);
router.use(videosRouter);
router.use("/scraper", scraperRouter);
router.use("/scraper/meesho", meeshoScraperRouter);
router.use("/scraper/amazon", amazonScraperRouter);
router.use("/price-mapper", priceMapperRouter);

export default router;
