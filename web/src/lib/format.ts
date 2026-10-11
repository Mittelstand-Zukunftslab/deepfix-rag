// Shared formatters: DD/MM/YYYY dates and European number formatting.
const dateFormatter = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
});

const currencyFormatter = new Intl.NumberFormat("de-DE", {
  style: "currency",
  currency: "USD",
});

const numberFormatter = new Intl.NumberFormat("de-DE", { maximumFractionDigits: 1 });

export const formatDate = (iso: string): string => dateFormatter.format(new Date(iso));
export const formatCurrency = (value: number): string => currencyFormatter.format(value);
export const formatNumber = (value: number): string => numberFormatter.format(value);
export const formatPercent = (value: number): string => `${numberFormatter.format(value)} %`;

export const formatFileSize = (bytes: number): string => {
  if (bytes >= 1_000_000) return `${numberFormatter.format(bytes / 1_000_000)} MB`;
  return `${Math.round(bytes / 1000)} KB`;
};
