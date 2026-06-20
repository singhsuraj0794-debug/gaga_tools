
#!/bin/bash
cd "$(dirname "$0")/artifacts/video-finder"
export PNPM_HOME="/Users/gajabmarketing/Library/pnpm"
export PATH="$PNPM_HOME/bin:$PATH"
echo "Starting video-finder dev server..."
exec npx vite --config vite.config.ts
