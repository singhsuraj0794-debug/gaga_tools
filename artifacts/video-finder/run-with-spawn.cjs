
const { spawn } = require('child_process');
const path = require('path');

console.log('Starting Vite with spawn...');

const vitePath = path.join(__dirname, '../../node_modules/.bin/vite');
console.log('Vite path:', vitePath);

const viteProcess = spawn(vitePath, ['--config', 'vite.config.ts'], {
  cwd: __dirname,
  stdio: 'inherit',
  shell: true,
  env: { ...process.env }
});

viteProcess.on('error', (err) => {
  console.error('Spawn error:', err);
});

viteProcess.on('exit', (code) => {
  console.log('Vite process exited with code:', code);
});

viteProcess.on('close', (code) => {
  console.log('Vite process closed with code:', code);
});
