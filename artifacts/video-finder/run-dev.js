
import { config } from "dotenv";
import { execFileSync } from "child_process";

config();

console.log("Starting vite with PORT:", process.env.PORT, "BASE_PATH:", process.env.BASE_PATH);
execFileSync("npx", ["vite", "--config", "vite.config.ts", "--host", "0.0.0.0"], {
  stdio: "inherit",
  cwd: process.cwd(),
});
