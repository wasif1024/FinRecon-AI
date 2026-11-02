import type { components } from "@/lib/api/schema";

export type CaseStatus = components["schemas"]["CaseStatus"];
export type Severity = components["schemas"]["Severity"];

/**
 * Formats the API's exact decimal string (e.g. "1.7900") without converting it to a float, which could round:
 * groups the integer part and keeps two decimals, or more when the extra digits are significant.
 */
export function formatMoney(amount: string | null | undefined, currency?: string | null): string {
  if (amount === null || amount === undefined || amount === "") return "—";
  const negative = amount.startsWith("-");
  const [whole, fraction = ""] = amount.replace(/^[-+]/, "").split(".");
  const trimmed = fraction.replace(/0+$/, "");
  const decimals = trimmed.length > 2 ? trimmed : trimmed.padEnd(2, "0");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "−" : ""}${grouped}.${decimals}${currency ? ` ${currency}` : ""}`;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value.length === 10 ? `${value}T00:00:00Z` : value);
  return date.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-GB", {
    day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZone: "UTC",
    timeZoneName: "short",
  });
}

/** "fee_discrepancy" → "Fee discrepancy" */
export function humanize(value: string | null | undefined): string {
  if (!value) return "—";
  const text = value.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}
