"use server";

import { refresh } from "next/cache";

import { ApiError, api, apiUpload, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";
import type { ActionResult } from "./cases";

type Batch = components["schemas"]["ImportBatchOut"];
type Run = components["schemas"]["RunOut"];
type Policy = components["schemas"]["PolicyOut"];

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_UPLOAD = 50 * 1024 * 1024; // the API checks again

async function run(work: () => Promise<string>): Promise<ActionResult> {
  try {
    const ok = await work();
    refresh();
    return { ok };
  } catch (error) {
    if (error instanceof ApiError) return { error: error.detail };
    throw error;
  }
}

function file(form: FormData): File {
  const value = form.get("file");
  if (!(value instanceof File) || value.size === 0) throw new ApiError(422, "Choose a file to upload.");
  if (value.size > MAX_UPLOAD) throw new ApiError(413, "That file is larger than 50 MB.");
  return value;
}

export async function uploadImport(_: ActionResult, form: FormData): Promise<ActionResult> {
  return run(async () => {
    const sourceId = String(form.get("source_id") ?? "");
    if (!UUID.test(sourceId)) throw new ApiError(400, "invalid source");
    const body = new FormData();
    body.append("file", file(form));
    const batch = await apiUpload<Batch>(`/sources/${sourceId}/imports`, body);
    return `${batch.file_name}: ${batch.accepted_records.toLocaleString()} accepted, ` +
      `${batch.rejected_records.toLocaleString()} rejected (${batch.status.replaceAll("_", " ")}).`;
  });
}

export async function startRun(_: ActionResult, form: FormData): Promise<ActionResult> {
  return run(async () => {
    const ids = form.getAll("batch_id").map(String).filter((id) => UUID.test(id));
    if (ids.length === 0) throw new ApiError(422, "Choose at least one import batch.");
    const result: Run = unwrap(await (await api()).POST("/reconciliation/runs", {
      body: { import_batch_ids: ids, open_cases: true },
    }));
    const exceptions = Object.values((result.summary as { exceptions?: Record<string, number> }).exceptions ?? {})
      .reduce((a, b) => a + b, 0);
    return `Run finished: ${exceptions} exceptions, ${result.cases_opened ?? 0} new cases opened.`;
  });
}

export async function uploadPolicy(_: ActionResult, form: FormData): Promise<ActionResult> {
  return run(async () => {
    const body = new FormData();
    body.append("file", file(form));
    const title = String(form.get("title") ?? "").trim();
    const policy = await apiUpload<Policy>(`/policies${title ? `?title=${encodeURIComponent(title)}` : ""}`, body);
    if (policy.status === "failed") throw new ApiError(422, policy.error ?? "The document could not be read.");
    return `${policy.title} v${policy.version} indexed (${policy.chunk_count} passages).`;
  });
}

export type ChainResult = { intact?: boolean; checked?: number; firstBad?: number | null; error?: string };

export async function verifyChain(): Promise<ChainResult> {
  try {
    const result = unwrap(await (await api()).GET("/audit-events/verify"));
    return { intact: result.intact, checked: result.events_checked, firstBad: result.first_bad_event_id };
  } catch (error) {
    if (error instanceof ApiError) return { error: error.detail };
    throw error;
  }
}
