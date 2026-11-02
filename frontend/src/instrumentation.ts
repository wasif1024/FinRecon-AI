import { registerOTel } from "@vercel/otel";

/**
 * OpenTelemetry for the dashboard's server (Next.js runs this once at startup). Off unless
 * OTEL_EXPORTER_OTLP_ENDPOINT is set, like the API. Spans for page renders, server actions and REST calls (fetch);
 * Connect calls get theirs from the interceptor in lib/rpc.ts. Both pass the trace on to the API, so a page view,
 * the RPCs it makes and their database queries are one trace.
 */
export function register() {
  if (!process.env.OTEL_EXPORTER_OTLP_ENDPOINT) return;
  const api = process.env.API_URL;
  registerOTel({
    serviceName: process.env.OTEL_SERVICE_NAME ?? "finrecon-dashboard",
    attributes: { "service.namespace": "finrecon" },
    instrumentationConfig: { fetch: { propagateContextUrls: api ? [new URL(api).origin] : [] } },
  });
}
