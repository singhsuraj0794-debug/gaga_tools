import express, { type Express } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import router from "./routes";
import { apiTokenGuard } from "./middlewares/apiToken";
import { logger } from "./lib/logger";

const app: Express = express();

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
// The API is served over a public tunnel. Keep CORS open by default (the web app
// and the box share one origin) but allow locking it down with CORS_ORIGINS
// (comma-separated). Credentials are never used, so a wildcard is harmless.
const corsOrigins = (process.env.CORS_ORIGINS || "")
  .split(",")
  .map((o) => o.trim())
  .filter(Boolean);
app.use(cors(corsOrigins.length > 0 ? { origin: corsOrigins } : {}));
app.use(express.json({ limit: "50mb" }));
app.use(express.urlencoded({ extended: true, limit: "50mb" }));

app.use(apiTokenGuard);
app.use("/api", router);

// A truncated request body (large catalogue POST over the tunnel) used to throw
// out of body-parser and kill the whole API. Answer 400 instead.
app.use((err: any, _req: any, res: any, _next: any) => {
  if (res.headersSent) return;
  const parseError = err?.type === "entity.parse.failed";
  const status = parseError ? 400 : (err?.status && err.status < 500 ? err.status : 500);
  res.status(status).json({
    error: parseError ? "malformed JSON body (request was truncated?)" : (err?.message || "server error"),
  });
});

export default app;
