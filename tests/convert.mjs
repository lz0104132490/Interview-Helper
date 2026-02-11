import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const samplePath = path.resolve(__dirname, 'sample.md');
const vendorPath = path.resolve(__dirname, '../server/public/vendor/marked.esm.js');

const { marked } = await import(pathToFileURL(vendorPath));

const input = fs.readFileSync(samplePath, 'utf8');
const output = marked.parse(input);

console.log(output);

