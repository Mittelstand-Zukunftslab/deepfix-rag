import type { GalleryDocument, GalleryQuery } from "@/schemas/document";
import { mockDelay } from "./mock-delay";

const BASE: GalleryDocument[] = [
  {
    id: "g-1",
    title: "Northwind Power & Light: Statement",
    kind: "Invoice",
    category: "utility",
    status: "OCR 99%",
    date: "2026-08-14",
    pages: 2,
    format: "PDF",
    sizeBytes: 384_000,
    total: 148.92,
    lines: [],
  },
  {
    id: "g-2",
    title: "Sterling Property: Lease agreement",
    kind: "Contract",
    category: "legal",
    status: "Signed",
    date: "2026-08-12",
    pages: 6,
    format: "PDF",
    sizeBytes: 1_400_000,
    lines: [],
  },
  {
    id: "g-3",
    title: "Kestrel Auto Service: Repair invoice",
    kind: "Invoice",
    category: "receipt",
    status: "Processed",
    date: "2026-08-10",
    pages: 1,
    format: "PDF",
    sizeBytes: 210_000,
    total: 555,
    lines: [
      { label: "Brake rotor replacement", amount: 280 },
      { label: "Synthetic fluid flush", amount: 65 },
      { label: "Labour (2,5 hrs)", amount: 210 },
    ],
  },
  {
    id: "g-4",
    title: "Meridian Savings Bank: Statement",
    kind: "Bank statement",
    category: "banking",
    status: "OCR 100%",
    date: "2026-07-02",
    pages: 4,
    format: "PDF",
    sizeBytes: 890_000,
    total: 12482.1,
    lines: [],
  },
  {
    id: "g-5",
    title: "Trailhead Outfitters: Camping gear",
    kind: "Receipt",
    category: "receipt",
    status: "Inbox",
    date: "2026-06-28",
    pages: 1,
    format: "JPG",
    sizeBytes: 118_000,
    total: 84.6,
    lines: [
      { label: "Drybag 30L", amount: 42 },
      { label: "Carabiner x4", amount: 18.5 },
      { label: "Trail rations", amount: 24.1 },
    ],
  },
  {
    id: "g-6",
    title: "Cascade Water District: Q2 meters",
    kind: "Utility",
    category: "utility",
    status: "OCR 98%",
    date: "2026-06-20",
    pages: 2,
    format: "PDF",
    sizeBytes: 412_000,
    total: 72.4,
    lines: [],
  },
  {
    id: "g-7",
    title: "Pinnacle Gas: Heating & supply",
    kind: "Utility",
    category: "utility",
    status: "Archived",
    date: "2026-06-15",
    pages: 1,
    format: "PDF",
    sizeBytes: 270_000,
    total: 64.12,
    lines: [],
  },
  {
    id: "g-8",
    title: "Telco Pacific: Dedicated trunk",
    kind: "Invoice",
    category: "invoice",
    status: "OCR 99%",
    date: "2026-06-01",
    pages: 3,
    format: "PDF",
    sizeBytes: 520_000,
    total: 374.2,
    lines: [
      { label: "SIP trunk unlimited", amount: 120 },
      { label: "Direct fiber 1Gbps", amount: 240 },
      { label: "Regulatory surcharge", amount: 14.2 },
    ],
  },
  {
    id: "g-9",
    title: "Aura Robotics: Mutual NDA",
    kind: "Contract",
    category: "legal",
    status: "Active",
    date: "2026-05-28",
    pages: 5,
    format: "PDF",
    sizeBytes: 680_000,
    lines: [],
  },
  {
    id: "g-10",
    title: "Summit Health: Annual panel",
    kind: "Medical",
    category: "medical",
    status: "Protected",
    date: "2026-05-19",
    pages: 2,
    format: "PDF",
    sizeBytes: 330_000,
    lines: [],
  },
  {
    id: "g-11",
    title: "Department of Revenue: Form 1040-ES",
    kind: "Tax form",
    category: "tax",
    status: "Archived",
    date: "2026-05-11",
    pages: 1,
    format: "PDF",
    sizeBytes: 180_000,
    total: 1450,
    lines: [],
  },
  {
    id: "g-12",
    title: "Depot Hardware: Store 901",
    kind: "Receipt",
    category: "receipt",
    status: "OCR 97%",
    date: "2026-04-29",
    pages: 1,
    format: "PDF",
    sizeBytes: 195_000,
    total: 150.19,
    lines: [
      { label: "Drywall fasteners x500", amount: 31.2 },
      { label: "Impact driver bits set", amount: 24.99 },
      { label: "Heavy duty primer 5gal", amount: 94 },
    ],
  },
];

// Expand the catalog to 36 documents so pagination is realistic.
const ALL: GalleryDocument[] = [0, 1, 2].flatMap((batch) =>
  BASE.map((d) => ({ ...d, id: `${d.id}-${batch}` })),
);

export const GALLERY_PAGE_SIZE = 12;

export const GALLERY_CATEGORIES = [
  { id: "all", label: "All" },
  { id: "invoices", label: "Invoices & receipts", match: ["invoice", "receipt", "utility"] },
  { id: "legal", label: "Contracts & legal", match: ["legal"] },
  { id: "banking", label: "Banking & statements", match: ["banking", "tax"] },
  { id: "medical", label: "Medical", match: ["medical"] },
] as const;

export interface GalleryPage {
  items: GalleryDocument[];
  total: number;
  counts: Record<string, number>;
}

const matchesCategory = (doc: GalleryDocument, categoryId?: string): boolean => {
  const cat = GALLERY_CATEGORIES.find((c) => c.id === categoryId);
  if (!cat || !("match" in cat)) return true;
  return (cat.match as readonly string[]).includes(doc.category);
};

export const galleryService = {
  async list(query: GalleryQuery): Promise<GalleryPage> {
    await mockDelay();
    const text = query.text?.trim().toLowerCase() ?? "";
    const filtered = ALL.filter((d) => matchesCategory(d, query.category)).filter(
      (d) => !text || `${d.title} ${d.kind} ${d.status}`.toLowerCase().includes(text),
    );
    const counts = Object.fromEntries(
      GALLERY_CATEGORIES.map((c) => [c.id, ALL.filter((d) => matchesCategory(d, c.id)).length]),
    );
    const start = (query.page - 1) * GALLERY_PAGE_SIZE;
    return {
      items: filtered.slice(start, start + GALLERY_PAGE_SIZE),
      total: filtered.length,
      counts,
    };
  },
};
