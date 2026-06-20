import { createServer, type IncomingMessage, type ServerResponse } from "node:http";
import app from "./index";

const server = createServer(app);

export default async function handler(
  req: IncomingMessage,
  res: ServerResponse
) {
  // Vercel handles the listening, we just pass the request to our express app
  return new Promise((resolve, reject) => {
    server.emit("request", req, res);
    res.on("finish", resolve);
    res.on("error", reject);
  });
}
