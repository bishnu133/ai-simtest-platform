/**
 * Server-side proxy: /api/engine/<path> → ${ENGINE_API_URL}/wizard/<path>
 *
 * The browser never talks to the engine directly. This keeps the engine URL
 * (and anything sent to it, such as a bot API key) off the client bundle and
 * lets the engine stay bound to localhost. Only the /wizard simulations + options surface is exposed.
 */
import type { NextRequest } from "next/server";

const ENGINE_API_URL = (process.env.ENGINE_API_URL ?? "http://127.0.0.1:8100").replace(/\/$/, "");

// First path segment must be one of these — nothing else on the engine is reachable.
const ALLOWED_ROOTS = new Set(["simulations", "options"]);

// Headers from the engine response that are safe and useful to pass through.
const PASSTHROUGH_HEADERS = ["content-type", "content-disposition", "content-length"];

async function proxy(request: NextRequest, ctx: RouteContext<"/api/engine/[...path]">) {
  const { path } = await ctx.params;
  if (!path.length || !ALLOWED_ROOTS.has(path[0]) || path.some((p) => p === ".." || p === ".")) {
    return Response.json({ detail: "Not found" }, { status: 404 });
  }

  const target = `${ENGINE_API_URL}/wizard/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const hasBody = request.method !== "GET" && request.method !== "HEAD";

  let upstream: Response;
  try {
    upstream = await fetch(target, {
      method: request.method,
      headers: hasBody ? { "content-type": "application/json" } : undefined,
      body: hasBody ? await request.text() : undefined,
      cache: "no-store",
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

  const headers = new Headers();
  for (const name of PASSTHROUGH_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers });
}

export const GET = proxy;
export const POST = proxy;
