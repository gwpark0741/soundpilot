// COCO compressed RLE -> row-major 0/1 mask. COCO counts run column-major.
function decodeCounts(s: string): number[] {
  const counts: number[] = [];
  let p = 0;
  while (p < s.length) {
    let x = 0, k = 0, more = 1;
    while (more) {
      const c = s.charCodeAt(p) - 48;
      x |= (c & 0x1f) << (5 * k);
      more = c & 0x20;
      p++; k++;
      if (!more && (c & 0x10)) x |= -1 << (5 * k);
    }
    if (counts.length > 2) x += counts[counts.length - 2];
    counts.push(x);
  }
  return counts;
}

export function decodeCocoRle(rleJson: string): { width: number; height: number; data: Uint8Array } {
  const { size, counts } = JSON.parse(rleJson) as { size: [number, number]; counts: string };
  const [height, width] = size;
  const data = new Uint8Array(height * width);
  let pos = 0;
  let value = 0;
  for (const run of decodeCounts(counts)) {
    if (value) {
      for (let i = pos; i < pos + run; i++) {
        data[(i % height) * width + Math.floor(i / height)] = 1;
      }
    }
    pos += run;
    value ^= 1;
  }
  return { width, height, data };
}

// Union of the given COCO RLE masks, sampled down to w x h (nearest), row-major 0/1.
// Walks the foreground runs only (no full-resolution decode), so it is cheap per frame.
// Assumes w <= mask width and h <= mask height.
export function rasterizeMasks(rles: string[], w: number, h: number): Uint8Array {
  const out = new Uint8Array(w * h);
  for (const rleJson of rles) {
    const { size, counts } = JSON.parse(rleJson) as { size: [number, number]; counts: string };
    const [H, W] = size;
    const rowMap = new Int32Array(H).fill(-1);
    const colMap = new Int32Array(W).fill(-1);
    for (let t = 0; t < h; t++) rowMap[Math.floor(((t + 0.5) * H) / h)] = t;
    for (let t = 0; t < w; t++) colMap[Math.floor(((t + 0.5) * W) / w)] = t;
    let pos = 0;
    let value = 0;
    for (const run of decodeCounts(counts)) {
      if (value) {
        const end = pos + run;
        for (let i = pos; i < end; ) {
          const col = Math.floor(i / H);
          const stop = Math.min(end, (col + 1) * H);
          const x = colMap[col];
          if (x >= 0) {
            for (let j = i; j < stop; j++) {
              const y = rowMap[j - col * H];
              if (y >= 0) out[y * w + x] = 1;
            }
          }
          i = stop;
        }
      }
      pos += run;
      value ^= 1;
    }
  }
  return out;
}
