import { Check, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDate, formatMoney, humanize } from "@/lib/format";
import type { Match, Transaction } from "./types";

export function Transactions({ transactions }: { transactions: Transaction[] }) {
  return (
    <div className="space-y-3">
      <div className="rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Source</TableHead>
              <TableHead>ID</TableHead>
              <TableHead>Reference</TableHead>
              <TableHead>Date</TableHead>
              <TableHead className="text-right">Amount</TableHead>
              <TableHead className="text-right">Fee</TableHead>
              <TableHead>Counterparty</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {transactions.map((t) => (
              <TableRow key={t.id}>
                <TableCell>{humanize(t.source_type)}</TableCell>
                <TableCell className="font-mono text-xs">{t.source_transaction_id ?? "—"}</TableCell>
                <TableCell className="font-mono text-xs">{t.external_reference ?? "—"}</TableCell>
                <TableCell>{formatDate(t.transaction_date)}</TableCell>
                <TableCell className="text-right tabular-nums">
                  {t.direction === "debit" ? "−" : ""}{formatMoney(t.amount, t.currency)}
                </TableCell>
                <TableCell className="text-right tabular-nums">{formatMoney(t.fee_amount)}</TableCell>
                <TableCell className="max-w-48 truncate" title={t.counterparty ?? ""}>{t.counterparty ?? t.merchant ?? "—"}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {transactions.map((t) => (
        <details key={t.id} className="rounded-md border px-3 py-2 text-sm">
          <summary className="cursor-pointer text-muted-foreground">
            Original record for {t.source_transaction_id ?? t.id} (as imported, untrusted)
          </summary>
          <pre className="mt-2 overflow-x-auto rounded bg-muted p-3 text-xs">{JSON.stringify(t.raw_payload, null, 2)}</pre>
        </details>
      ))}
    </div>
  );
}

type Rule = {
  rule: string; passed: boolean; left?: unknown; right?: unknown; difference?: string; lag_days?: number;
  window?: [number, number]; note?: string; explains_amount_difference?: boolean;
};

export function MatchExplanations({ matches }: { matches: Match[] }) {
  if (matches.length === 0) {
    return <p className="text-sm text-muted-foreground">The engine never paired these transactions with a counterpart.</p>;
  }
  return (
    <div className="space-y-4">
      {matches.map((m) => {
        const explanation = m.explanation as { leg?: string; summary?: string; rules?: Rule[] };
        return (
          <div key={m.id} className="rounded-lg border">
            <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2 text-sm">
              <span className="font-medium">{humanize(explanation.leg ?? "match")}</span>
              <Badge variant="secondary">{humanize(m.classification)}</Badge>
              <span className="text-muted-foreground">{humanize(m.strategy)} · score {Number(m.score).toFixed(2)}</span>
              {explanation.summary && <span className="ml-auto text-xs text-muted-foreground">{explanation.summary}</span>}
            </div>
            {explanation.rules && (
              <Table>
                <TableBody>
                  {explanation.rules.map((rule) => (
                    <TableRow key={rule.rule}>
                      <TableCell className="w-8">
                        {rule.passed ? <Check className="size-4 text-emerald-600" aria-label="passed" />
                                     : <X className="size-4 text-rose-600" aria-label="failed" />}
                      </TableCell>
                      <TableCell className="font-medium">{humanize(rule.rule)}</TableCell>
                      <TableCell className="font-mono text-xs">{display(rule.left)}</TableCell>
                      <TableCell className="font-mono text-xs">{display(rule.right)}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">{detail(rule)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </div>
        );
      })}
    </div>
  );
}

function display(value: unknown): string {
  return value === undefined || value === null ? "—" : String(value);
}

function detail(rule: Rule): string {
  if (rule.note) return rule.note;
  if (rule.lag_days !== undefined && rule.window) return `${rule.lag_days} days (allowed ${rule.window[0]}–${rule.window[1]})`;
  if (rule.difference !== undefined) {
    return `difference ${rule.difference}${rule.explains_amount_difference ? " · explains the amount difference" : ""}`;
  }
  return "";
}
