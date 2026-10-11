import type { UploadedFile } from "@/schemas/upload";
import { mockDelay } from "./mock-delay";

const minutesAgo = (m: number): string => new Date(Date.now() - m * 60_000).toISOString();

let files: UploadedFile[] = [
  {
    id: "u-1",
    name: "Annual_Commercial_Lease_Agreement_2026.pdf",
    sizeBytes: 4_800_000,
    uploadedAt: minutesAgo(3),
    kind: "document",
    status: "processed",
    progress: 100,
  },
  {
    id: "u-2",
    name: "MetLife_Medical_Policy_Schedule.pdf",
    sizeBytes: 1_200_000,
    uploadedAt: minutesAgo(12),
    kind: "document",
    status: "processed",
    progress: 100,
  },
  {
    id: "u-3",
    name: "Server_Rack_Delivery_Receipt.jpg",
    sizeBytes: 8_600_000,
    uploadedAt: minutesAgo(25),
    kind: "image",
    status: "processing",
    progress: 85,
  },
  {
    id: "u-4",
    name: "Vendor_Tax_Compliance_Form_W9.pdf",
    sizeBytes: 640_000,
    uploadedAt: minutesAgo(60),
    kind: "document",
    status: "processed",
    progress: 100,
  },
];

export const uploadService = {
  async list(): Promise<UploadedFile[]> {
    await mockDelay();
    return files;
  },
  async upload(input: File[]): Promise<UploadedFile[]> {
    await mockDelay(400);
    const created: UploadedFile[] = input.map((f) => ({
      id: crypto.randomUUID(),
      name: f.name,
      sizeBytes: f.size,
      uploadedAt: new Date().toISOString(),
      kind: f.type.startsWith("image/") ? "image" : "document",
      status: "processing",
      progress: 10,
    }));
    files = [...created, ...files];
    return created;
  },
  async remove(id: string): Promise<void> {
    await mockDelay(150);
    files = files.filter((f) => f.id !== id);
  },
};
