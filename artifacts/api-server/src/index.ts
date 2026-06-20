import dotenv from "dotenv";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

dotenv.config({ path: path.join(__dirname, "..", ".env") });

const { default: app } = await import("./app");
const { logger } = await import("./lib/logger");
const { warmUp } = await import("./lib/productScraper");
const { loadExistingDownloads } = await import("./lib/downloadManager");

const rawPort = process.env["PORT"];

if (!rawPort) {
  throw new Error(
    "PORT environment variable is required but was not provided.",
  );
}

const port = Number(rawPort);

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

app.listen(port, (err) => {
  if (err) {
    logger.error({ err }, "Error listening on port");
    process.exit(1);
  }

  logger.info({ port }, "Server listening");

  // Register files from previous sessions so they are immediately playable
  loadExistingDownloads();

  warmUp().catch((e) =>
    logger.warn({ err: e }, "Startup warm-up failed")
  );
});
