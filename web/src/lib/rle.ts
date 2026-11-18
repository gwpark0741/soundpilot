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
