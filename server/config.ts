import { config } from 'dotenv';
config({ path: '.env.local', quiet: true });
export const settings = {
  key: process.env.DEEPSEEK_API_KEY || '',
  model: process.env.DEEPSEEK_MODEL || 'deepseek-flash',
  dataDir: process.env.DATA_DIR || 'runtime',
  port: Number(process.env.PORT || 3001),
};
