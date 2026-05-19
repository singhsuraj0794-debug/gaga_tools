import { Router, type IRouter } from "express";
import healthRouter from "./health";
import productsRouter from "./products";
import videosRouter from "./videos";

const router: IRouter = Router();

router.use(healthRouter);
router.use(productsRouter);
router.use(videosRouter);

export default router;
