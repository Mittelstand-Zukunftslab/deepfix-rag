import { createDocument, deleteDocument, listDocuments, type DocumentResponse } from "@/client";

import { readPdf } from "./pdf-parse";

export type LibraryDocument = DocumentResponse;

export class LibraryRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LibraryRequestError";
  }
}

function errorDetail(error: unknown): string {
  if (
    error &&
    typeof error === "object" &&
    "detail" in error &&
    typeof error.detail === "string" &&
    error.detail.length > 0
  ) {
    return error.detail;
  }
  return "Unable to reach the server. Try again.";
}

export const libraryService = {
  async list(): Promise<LibraryDocument[]> {
    try {
      const result = await listDocuments();
      if (result.error || !result.data) throw new LibraryRequestError(errorDetail(result.error));
      return result.data;
    } catch (error) {
      if (error instanceof LibraryRequestError) throw error;
      throw new LibraryRequestError("Unable to reach the server. Try again.");
    }
  },

  async upload(file: File): Promise<LibraryDocument> {
    try {
      const reading = file.name.toLowerCase().endsWith(".pdf")
        ? JSON.stringify(await readPdf(file))
        : undefined;
      const result = await createDocument({ body: { file, reading } });
      if (result.error || !result.data) throw new LibraryRequestError(errorDetail(result.error));
      return result.data;
    } catch (error) {
      if (error instanceof LibraryRequestError) throw error;
      throw new LibraryRequestError("Unable to read this PDF. Try another file.");
    }
  },

  async remove(docId: string): Promise<void> {
    try {
      const result = await deleteDocument({ path: { doc_id: docId } });
      if (!result.response?.ok) throw new LibraryRequestError(errorDetail(result.error));
    } catch (error) {
      if (error instanceof LibraryRequestError) throw error;
      throw new LibraryRequestError("Unable to reach the server. Try again.");
    }
  },
};
