import type { DescEnum } from "@bufbuild/protobuf";
import { timestampDate, type Timestamp } from "@bufbuild/protobuf/wkt";

/**
 * An RPC enum value as the API's own value: CaseStatus.AWAITING_APPROVAL → "awaiting_approval" (the proto names
 * are the database values, upper-cased behind the enum's prefix). "" for UNSPECIFIED or an unknown number.
 */
export function enumValue(schema: DescEnum, value: number): string {
  const found = schema.values.find((v) => v.number === value);
  return found && value !== 0 ? found.localName.toLowerCase() : "";
}

/** The reverse of enumValue: "awaiting_approval" → CaseStatus.AWAITING_APPROVAL, or undefined. */
export function enumFrom<T extends number>(schema: DescEnum, value: string | undefined): T | undefined {
  const found = value ? schema.values.find((v) => v.number !== 0 && v.localName === value.toUpperCase()) : undefined;
  return found?.number as T | undefined;
}

/** CaseStatus.AWAITING_APPROVAL → "Awaiting approval" */
export function enumLabel(schema: DescEnum, value: number): string {
  return humanize(enumValue(schema, value));
}

type When = string | Timestamp | null | undefined;

function iso(value: When): string | undefined {
  if (!value) return undefined;
  return typeof value === "string" ? value : timestampDate(value).toISOString();
}

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

export function formatDate(when: When): string {
  const value = iso(when);
  if (!value) return "—";
  const date = new Date(value.length === 10 ? `${value}T00:00:00Z` : value);
  return date.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: "UTC" });
}

export function formatDateTime(when: When): string {
  const value = iso(when);
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
