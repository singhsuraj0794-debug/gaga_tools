
const { createServer } = require('vite');
const path = require('path');

async function main() {
  console.log('Starting Vite server for video-finder...');
  console.log('CWD:', process.cwd());

  const server = await createServer({
    configFile: path.resolve(__dirname, 'vite.config.ts'),
  });

  await server.listen();

  console.log('✅ Vite server started on port:', server.config.server.port);
  console.log('🎉 Local URL:', `http://localhost:${server.config.server.port}`);
}

main().catch(err => {
  console.error('❌ Error starting Vite:', err);
  process.exit(1);
});
