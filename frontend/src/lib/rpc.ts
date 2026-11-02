import "server-only";

import { Code, ConnectError, createClient, type Interceptor, type Transport } from "@connectrpc/connect";
import { createConnectTransport } from "@connectrpc/connect-node";
import { context, propagation, SpanKind, SpanStatusCode, trace } from "@opentelemetry/api";
import { redirect } from "next/navigation";
import { connection } from "next/server";

import { ApprovalService } from "@/gen/finrecon/v1/approvals_pb";
import { CaseService } from "@/gen/finrecon/v1/cases_pb";
import { IdentityService } from "@/gen/finrecon/v1/identity_pb";
import { ApiError } from "@/lib/api/client";
import { getToken } from "@/lib/session";

// Connect (binary protobuf over HTTP) to the API's RPC services, server-to-server. The contract is proto/finrecon/v1;
// the clients are generated (pnpm gen:rpc). REST (lib/api/client.ts) remains for uploads, downloads and the
// insight pages.

const STATUS: Partial<Record<Code, number>> = {
  [Code.InvalidArgument]: 422, [Code.Unauthenticated]: 401, [Code.PermissionDenied]: 403, [Code.NotFound]: 404,
  [Code.FailedPrecondition]: 409, [Code.AlreadyExists]: 409, [Code.ResourceExhausted]: 429,
};

const withToken: Interceptor = (next) => async (request) => {
  const token = await getToken();
  if (token) request.header.set("Authorization", `Bearer ${token}`);
  return next(request);
};

const tracer = trace.getTracer("finrecon-dashboard");

/**
 * A client span per RPC, and the trace context passed to the API (W3C traceparent), so the API's spans join the
 * page's trace. No-ops unless OpenTelemetry is on (src/instrumentation.ts).
 */
const traced: Interceptor = (next) => (request) => {
  const service = request.service.typeName;
  const method = request.method.name;
  return tracer.startActiveSpan(`${service}/${method}`, {
    kind: SpanKind.CLIENT, attributes: { "rpc.system": "connect_rpc", "rpc.service": service, "rpc.method": method },
  }, async (span) => {
    propagation.inject(context.active(), request.header, { set: (header, key, value) => header.set(key, value) });
    try {
      return await next(request);
    } catch (error) {
      span.setStatus({ code: SpanStatusCode.ERROR });
      span.setAttribute("rpc.connect_rpc.error_code", Code[ConnectError.from(error).code]);
      throw error;
    } finally {
      span.end();
    }
  });
};

/**
 * Turns RPC errors into the same ApiError (with an HTTP-like status) as REST. Done around each call, not in an
 * interceptor: Connect wraps whatever an interceptor throws (including Next's redirect) in a ConnectError.
 */
function guarded<T extends object>(client: T): T {
  return new Proxy(client, {
    get(target, property, receiver) {
      const method = Reflect.get(target, property, receiver);
      if (typeof method !== "function") return method;
      return async (...args: unknown[]) => {
        try {
          return await method.apply(target, args);
        } catch (error) {
          const failure = ConnectError.from(error);
          // An expired session sends the user to sign in again; a failed sign-in is the login form's to report.
          if (failure.code === Code.Unauthenticated && property !== "login") redirect("/login?expired=1");
          throw new ApiError(STATUS[failure.code] ?? 500, failure.rawMessage);
        }
      };
    },
  });
}

let transport: Transport | undefined;

function getTransport(): Transport {
  const baseUrl = process.env.API_URL;
  if (!baseUrl) throw new Error("API_URL is not set");
  transport ??= createConnectTransport({ baseUrl, httpVersion: "1.1", useBinaryFormat: true, interceptors: [traced, withToken] });
  return transport;
}

/**
 * Typed clients for the current request, authenticated with the session cookie. Like api(), marks the caller as
 * dynamic first: tenant data is never prerendered or cached on the server.
 */
export async function rpc() {
  await connection();
  const transport = getTransport();
  return {
    identity: guarded(createClient(IdentityService, transport)),
    cases: guarded(createClient(CaseService, transport)),
    approvals: guarded(createClient(ApprovalService, transport)),
  };
}
