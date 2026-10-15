// Builds per-page head metadata. Mirrors Next.js `generateMetadata` for easy migration.
export const pageMeta = (title: string, description: string) => [
  { title: `${title} — Papergnx.ai` },
  { name: "description", content: description },
  { property: "og:title", content: `${title} — Papergnx.ai` },
  { property: "og:description", content: description },
];
