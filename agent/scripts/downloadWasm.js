/**
 * Downloads Tree-sitter WASM grammar files for supported languages.
 * Runs automatically after npm install via postinstall script.
 */
const https = require('https');
const fs = require('fs');
const path = require('path');

const WASM_DIR = path.join(__dirname, '..', 'wasm');

// WASM grammar files hosted on the tree-sitter releases CDN / npm package dist
// Using tree-sitter-javascript and tree-sitter-typescript npm package WASM files
const WASM_FILES = [
  {
    url: 'https://unpkg.com/tree-sitter-javascript@0.25.0/tree-sitter-javascript.wasm',
    filename: 'tree-sitter-javascript.wasm'
  },
  {
    url: 'https://unpkg.com/tree-sitter-typescript@0.23.2/tree-sitter-typescript.wasm',
    filename: 'tree-sitter-typescript.wasm'
  },
  {
    url: 'https://unpkg.com/tree-sitter-typescript@0.23.2/tree-sitter-tsx.wasm',
    filename: 'tree-sitter-tsx.wasm'
  }
];

function downloadFile(url, dest) {
  return new Promise((resolve, reject) => {
    if (fs.existsSync(dest)) {
      console.log(`  Already exists: ${path.basename(dest)}`);
      return resolve();
    }

    const file = fs.createWriteStream(dest);
    const request = (requestUrl) => {
      https.get(requestUrl, (response) => {
        // Follow redirects
        if (response.statusCode === 301 || response.statusCode === 302) {
          file.close();
          return request(response.headers.location);
        }
        if (response.statusCode !== 200) {
          file.close();
          fs.unlink(dest, () => {});
          return reject(new Error(`Failed to download ${requestUrl}: HTTP ${response.statusCode}`));
        }
        response.pipe(file);
        file.on('finish', () => {
          file.close(resolve);
        });
      }).on('error', (err) => {
        file.close();
        fs.unlink(dest, () => {});
        reject(err);
      });
    };
    request(url);
  });
}

async function main() {
  if (!fs.existsSync(WASM_DIR)) {
    fs.mkdirSync(WASM_DIR, { recursive: true });
  }

  // Also copy the tree-sitter.wasm from web-tree-sitter package
  const webTreeSitterWasm = path.join(__dirname, '..', 'node_modules', 'web-tree-sitter', 'tree-sitter.wasm');
  const localTreeSitterWasm = path.join(WASM_DIR, 'tree-sitter.wasm');
  if (fs.existsSync(webTreeSitterWasm) && !fs.existsSync(localTreeSitterWasm)) {
    fs.copyFileSync(webTreeSitterWasm, localTreeSitterWasm);
    console.log('  Copied: tree-sitter.wasm');
  }

  console.log('\nDownloading Tree-sitter WASM grammar files...');

  for (const { url, filename } of WASM_FILES) {
    const dest = path.join(WASM_DIR, filename);
    try {
      process.stdout.write(`  Downloading ${filename}... `);
      await downloadFile(url, dest);
      console.log('OK');
    } catch (err) {
      console.log(`FAILED: ${err.message}`);
      console.warn(`  Warning: Could not download ${filename}. Parser will skip ${filename.replace('tree-sitter-','').replace('.wasm','')} files.`);
    }
  }

  console.log('WASM grammar download complete.\n');
}

main().catch(console.error);
