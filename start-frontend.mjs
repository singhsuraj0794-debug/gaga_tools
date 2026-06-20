
import { createServer } from 'vite';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

console.log('Starting video-finder Vite server...');

const viteConfigPath = path.resolve(__dirname, 'artifacts/video-finder/vite.config.ts');

const server = await createServer({
  configFile: viteConfigPath,
  root: path.resolve(__dirname, 'artifacts/video-finder'),
});

await server.listen();

console.log('✅ Video Finder is running!');
console.log('🎉 URL:', `http://localhost:${server.config.server.port}`);
