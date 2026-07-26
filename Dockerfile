FROM node:20-bookworm-slim

# ── System dependencies ──────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    ffmpeg curl ca-certificates git \
    # Playwright browser runtime deps
    fonts-liberation libnss3 libnspr4 libatk-bridge2.0-0 libdrm2 \
    libxkbcommon0 libxcomposite1 libxdamage1 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libx11-xcb1 libxcb-dri3-0 libxss1 libxtst6 libcups2 \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ──────────────────────────────────────────
COPY requirements-docker.txt .
RUN pip3 install --break-system-packages --no-cache-dir -r requirements-docker.txt

# ── Playwright Chromium ──────────────────────────────────────────
RUN python3 -m playwright install chromium && \
    python3 -m playwright install-deps chromium

# ── yt-dlp ───────────────────────────────────────────────────────
RUN curl -L https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
    -o /usr/local/bin/yt-dlp && chmod a+rx /usr/local/bin/yt-dlp

# ── pnpm ─────────────────────────────────────────────────────────
RUN corepack enable && corepack prepare pnpm@9.15.0 --activate

WORKDIR /app

# ── Copy workspace files ─────────────────────────────────────────
COPY pnpm-workspace.yaml pnpm-lock.yaml package.json tsconfig.json tsconfig.base.json ./
COPY lib/ ./lib/
COPY artifacts/api-server/ ./artifacts/api-server/
COPY scripts/ ./scripts/

# ── Fix platform-specific deps (Linux x64, not darwin) ───────────
# The api-server package.json directly lists darwin-arm64 native deps
# that won't install on Linux. Remove them before pnpm install.
RUN sed -i \
    -e '/"@esbuild\/darwin-arm64"/d' \
    -e '/"@rollup\/rollup-darwin-arm64"/d' \
    -e '/"@tailwindcss\/oxide-darwin-arm64"/d' \
    -e '/"lightningcss-darwin-arm64"/d' \
    artifacts/api-server/package.json

# ── Install & build ──────────────────────────────────────────────
RUN pnpm install && \
    pnpm --filter @workspace/api-server run build

# ── Fix yt-dlp: remove --cookies-from-browser (won't work server-side) ─
# This is embedded in the bundled output; we patch it at the source
RUN sed -i 's/--cookies-from-browser", "chrome", //' \
    artifacts/api-server/dist/index.mjs 2>/dev/null || true

# ── Runtime env ──────────────────────────────────────────────────
ENV PORT=8080
ENV NODE_ENV=production
ENV YT_DLP_PATH=/usr/local/bin/yt-dlp
ENV FFMPEG_PATH=/usr/bin/ffmpeg
ENV PYTHON_BIN=python3

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8080/api/healthz || exit 1

CMD ["node", "artifacts/api-server/dist/index.mjs"]
