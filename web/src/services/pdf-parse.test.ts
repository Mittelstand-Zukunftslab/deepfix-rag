import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { initSync } from "@llamaindex/liteparse-wasm";
import { describe, expect, it } from "vitest";

import { readPdf } from "./pdf-parse";

const wasmPath = resolve(
  process.cwd(),
  "node_modules/@llamaindex/liteparse-wasm/pkg/liteparse_wasm_bg.wasm",
);
initSync({ module: readFileSync(wasmPath) });

const encoder = new TextEncoder();

function concat(parts: Uint8Array[]): Uint8Array {
  const size = parts.reduce((sum, part) => sum + part.length, 0);
  const out = new Uint8Array(size);
  let offset = 0;
  for (const part of parts) {
    out.set(part, offset);
    offset += part.length;
  }
  return out;
}

function buildPdf(objects: Uint8Array[]): Uint8Array {
  const chunks: Uint8Array[] = [encoder.encode("%PDF-1.4\n")];
  const offsets = [0];
  objects.forEach((object, index) => {
    offsets.push(chunks.reduce((sum, part) => sum + part.length, 0));
    chunks.push(encoder.encode(`${index + 1} 0 obj\n`), object, encoder.encode("\nendobj\n"));
  });
  const xref = chunks.reduce((sum, part) => sum + part.length, 0);
  let table = `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (let index = 1; index < offsets.length; index += 1) {
    table += `${String(offsets[index]).padStart(10, "0")} 00000 n \n`;
  }
  table += `trailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  chunks.push(encoder.encode(table));
  return concat(chunks);
}

function textPdf(text: string): Uint8Array {
  const stream = `BT /F1 24 Tf 72 700 Td (${text}) Tj ET`;
  return buildPdf([
    encoder.encode("<< /Type /Catalog /Pages 2 0 R >>"),
    encoder.encode("<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
    encoder.encode(
      "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
    ),
    encoder.encode("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"),
    encoder.encode(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`),
  ]);
}

function scannedPdf(): Uint8Array {
  const pixels = new Uint8Array(64).fill(180);
  const content = "q\n612 0 0 792 0 0 cm\n/Im0 Do\nQ\n";
  return buildPdf([
    encoder.encode("<< /Type /Catalog /Pages 2 0 R >>"),
    encoder.encode("<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
    encoder.encode(
      "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /XObject << /Im0 4 0 R >> >> /Contents 5 0 R >>",
    ),
    concat([
      encoder.encode(
        "<< /Type /XObject /Subtype /Image /Width 8 /Height 8 /ColorSpace /DeviceGray /BitsPerComponent 8 /Length 64 >>\nstream\n",
      ),
      pixels,
      encoder.encode("\nendstream"),
    ]),
    encoder.encode(`<< /Length ${content.length} >>\nstream\n${content}\nendstream`),
  ]);
}

describe("Wasm PDF reading", () => {
  it("extracts text and leaves a normal page unmarked", async () => {
    const reading = await readPdf(textPdf("The committee approved the annual harbor budget."));
    expect(reading.text).toBe("The committee approved the annual harbor budget.");
    expect(reading.pages).toEqual([]);
  });

  it("marks a full-page image with no text as scanned", async () => {
    const reading = await readPdf(scannedPdf());
    expect(reading.text.trim()).toBe("");
    expect(reading.pages).toEqual([{ page: 1, reasons: ["scanned"] }]);
  });

  it("marks a vowel-starved text layer as garbled", async () => {
    const text = "bcdfghjklmnpqrstvwxyz bcdfghjklmnpqrstvwxyz";
    const reading = await readPdf(textPdf(text));
    expect(reading.text).toBe(text);
    expect(reading.pages).toEqual([{ page: 1, reasons: ["garbled"] }]);
  });
});
