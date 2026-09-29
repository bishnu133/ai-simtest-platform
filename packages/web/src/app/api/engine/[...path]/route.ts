/**
 * Server-side proxy: /api/engine/<path> → ${ENGINE_API_URL}/wizard/<path>
 *
 * The browser never talks to the engine directly. This keeps the engine URL
 * (and anything sent to it, such as a bot API key) off the client bundle and
 * lets the engine stay bound to localhost. Only the /wizard simulations, options, regression suites,
 * run comparison, calibration and notification settings surface is exposed.
 */
import type { NextRequest } from "next/server";

const ENGINE_API_URL = (process.env.ENGINE_API_URL ?? "http://127.0.0.1:8100").replace(/\/$/, "");
// The engine's API token (SIMTEST_API_TOKEN on the engine). Server-side only:
// never in a NEXT_PUBLIC_ variable, never sent to the browser.
const ENGINE_API_TOKEN = process.env.ENGINE_API_TOKEN?.trim() || "";

// First path segment must be one of these — nothing else on the engine is reachable.
const ALLOWED_ROOTS = new Set(["simulations", "options", "suites", "compare-runs", "calibration", "notifications", "ci", "ai-settings", "bot-check", "bots", "personas", "templates", "library"]);
// DELETE is only for removing a saved regression suite, bot, persona, template or your own policy/workflow
const DELETABLE_ROOTS = new Set(["suites", "bots", "personas", "templates", "library"]);
// PUT is only for saving notification and AI settings, and editing a saved bot, template or policy/workflow
const PUTTABLE_ROOTS = new Set(["notifications", "ai-settings", "bots", "templates", "library"]);

// Headers from the engine response that are safe and useful to pass through.
// cache-control and x-accel-buffering keep the live run stream (text/event-stream) unbuffered.
const PASSTHROUGH_HEADERS = ["content-type", "content-disposition", "content-length", "cache-control", "x-accel-buffering"];

async function proxy(request: NextRequest, ctx: RouteContext<"/api/engine/[...path]">) {
  const { path } = await ctx.params;
  if (!path.length || !ALLOWED_ROOTS.has(path[0]) || path.some((p) => p === ".." || p === ".")) {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }
  if (
    (request.method === "DELETE" && !DELETABLE_ROOTS.has(path[0])) ||
    (request.method === "PUT" && !PUTTABLE_ROOTS.has(path[0]))
  ) {
    return Response.json({ detail: "Not allowed" }, { status: 405 });
  }

  const target = `${ENGINE_API_URL}/wizard/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  let upstream: Response;
  try {
    const headers: Record<string, string> = {};
    if (hasBody) headers["content-type"] = "application/json";
    if (ENGINE_API_TOKEN) headers.authorization = `Bearer ${ENGINE_API_TOKEN}`;
    upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? await request.text() : undefined,
      cache: "no-store",
      // Closing the page ends the engine's side too (the live stream would otherwise run on)
      signal: request.signal,
    });
  } catch {
    return Response.json(
      {
        detail: "Cannot reach the AI SimTest engine. Start it with `API_HOST=127.0.0.1 API_PORT=8100 simtest serve`.",
        code: "engine_unreachable",
      },
      { status: 502 },
    );
  }

  if (upstream.status === 401) {
    return Response.json(
      {
        detail: ENGINE_API_TOKEN
          ? "The engine refused ENGINE_API_TOKEN: it must match the engine's SIMTEST_API_TOKEN."
          : "The engine requires an API token: set ENGINE_API_TOKEN (the engine's SIMTEST_API_TOKEN) for this dashboard.",
        code: "engine_unauthorized",
      },
      { status: 502 },
    );
  }
  const headers = new Headers();
  for (const name of PASSTHROUGH_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  // A 204 must not carry a body
  return new Response(upstream.status === 204 ? null : upstream.body, { status: upstream.status, headers });
}

export const GET = proxy;
export const POST = proxy;
export const DELETE = proxy;
export const PUT = proxy;
