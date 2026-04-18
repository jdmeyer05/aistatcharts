// One-shot script: generate PWA icons from public/favicon.png.
// Run with: node scripts/generate-pwa-icons.mjs
//
// Produces:
//   public/icon-192.png         — 192×192 launcher icon
//   public/icon-512.png         — 512×512 launcher icon
//   public/icon-512-maskable.png — 512×512 with ~20% safe zone padding
//                                   (Android adaptive icon mask)
import sharp from "sharp";
import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const publicDir = resolve(__dirname, "..", "public");
const source = resolve(publicDir, "favicon.png");

const src = readFileSync(source);

// 192×192
await sharp(src).resize(192, 192, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
  .png().toFile(resolve(publicDir, "icon-192.png"));

// 512×512 (source is already 512×512 but re-encode through sharp for consistency)
await sharp(src).resize(512, 512).png().toFile(resolve(publicDir, "icon-512.png"));

// 512×512 maskable: 20% padding on each side = icon occupies 60% of canvas safely.
// Android's adaptive icon mask crops the outer ~10-18% into a shape; placing the
// content in the inner 60% guarantees no critical detail is ever clipped.
const inner = 512 * 0.6; // 307
const inset = Math.round((512 - inner) / 2); // 102

const innerBuffer = await sharp(src).resize(Math.round(inner), Math.round(inner)).png().toBuffer();

await sharp({
  create: {
    width: 512,
    height: 512,
    channels: 4,
    background: "#1a2332", // matches light-mode theme-color from layout.tsx
  },
})
  .composite([{ input: innerBuffer, left: inset, top: inset }])
  .png()
  .toFile(resolve(publicDir, "icon-512-maskable.png"));

console.log("Generated: icon-192.png, icon-512.png, icon-512-maskable.png");
