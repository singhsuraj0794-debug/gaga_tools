import { Router, type IRouter } from "express";
import healthRouter from "./health";
import productsRouter from "./products";
import videosRouter from "./videos";
import scraperRouter from "./scraper";

const router: IRouter = Router();

router.use(healthRouter);
router.use(productsRouter);
router.use(videosRouter);
router.use("/scraper", scraperRouter);

export default router;
