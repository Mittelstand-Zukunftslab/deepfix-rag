import init, { LiteParse } from "@llamaindex/liteparse-wasm";

export interface PdfPageReading {
  page: number;
  reasons: Array<"scanned" | "garbled">;
}

export interface PdfReading {
  text: string;
  pages: PdfPageReading[];
}

let ready: Promise<void> | null = null;

export function ensureLiteParse(): Promise<void> {
  ready ??= init().then(() => undefined);
  return ready;
}

export async function readPdf(file: Blob | Uint8Array): Promise<PdfReading> {
  await ensureLiteParse();
  const bytes = file instanceof Uint8Array ? file : new Uint8Array(await file.arrayBuffer());
  const parser = new LiteParse({ ocrEnabled: false, outputFormat: "text", quiet: true });
  try {
    const parsed = await parser.parse(bytes);
    const complex = await parser.isComplex(bytes);
    const pages: PdfPageReading[] = [];
    for (const page of complex) {
      const reasons = page.reasons.filter(
        (reason): reason is "scanned" | "garbled" => reason === "scanned" || reason === "garbled",
      );
      if (reasons.length > 0) {
        pages.push({ page: page.pageNumber, reasons });
      }
    }
    return { text: parsed.text ?? "", pages };
  } finally {
    parser.free();
  }
}
