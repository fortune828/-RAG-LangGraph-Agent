import express from 'express';
import { resolve } from 'node:path';
import { createApp } from './app';
import { settings } from './config';
const app = createApp();
if (process.env.NODE_ENV === 'production') {
  app.use(express.static(resolve('dist')));
  app.get('*', (_req, res) => res.sendFile(resolve('dist/index.html')));
} else {
  const { createServer } = await import('vite');
  const vite = await createServer({ server: { middlewareMode: true }, appType: 'spa' });
  app.use(vite.middlewares);
}
app.listen(settings.port, '127.0.0.1', () => console.log(`Yijing Agent: http://127.0.0.1:${settings.port}`));
