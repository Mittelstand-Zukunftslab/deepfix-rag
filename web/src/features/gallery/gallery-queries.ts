import { queryOptions } from "@tanstack/react-query";

import type { GalleryQuery } from "@/schemas/document";
import { galleryService } from "@/services/gallery-service";

export const galleryQueryOptions = (query: GalleryQuery) =>
  queryOptions({ queryKey: ["gallery", query], queryFn: () => galleryService.list(query) });

export const initialGalleryQueryOptions = () =>
  galleryQueryOptions({ category: "all", text: "", page: 1 });
