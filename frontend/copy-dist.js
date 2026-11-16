import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const distDir = path.join(__dirname, 'dist');
const targetDir = __dirname;

function copyRecursive(src, dest) {
  if (!fs.existsSync(src)) return;
  const stat = fs.statSync(src);
  if (stat.isDirectory()) {
    if (!fs.existsSync(dest)) fs.mkdirSync(dest, { recursive: true });
    for (const item of fs.readdirSync(src)) {
      copyRecursive(path.join(src, item), path.join(dest, item));
    }
  } else {
    fs.copyFileSync(src, dest);
  }
}

// 1. Copy assets to frontend/assets
copyRecursive(path.join(distDir, 'assets'), path.join(targetDir, 'assets'));

// 2. Read dist/app.html and write to frontend/index.html AND dist/index.html
const appHtmlPath = path.join(distDir, 'app.html');
if (fs.existsSync(appHtmlPath)) {
  const content = fs.readFileSync(appHtmlPath, 'utf8');
  fs.writeFileSync(path.join(targetDir, 'index.html'), content, 'utf8');
  fs.writeFileSync(path.join(distDir, 'index.html'), content, 'utf8');
  console.log('[copy-dist] Successfully updated index.html in frontend/ and dist/');
}

