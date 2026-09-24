import { NextRequest } from "next/server";

const API = process.env.API_INTERNAL_URL ?? "http://127.0.0.1:8000";
const BEARER = process.env.DEV_AUTH_BEARER ?? "dev";
const USER = process.env.DEV_AUTH_USER_ID ?? "dev_actor_1";

export async function GET(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  const { path } = await ctx.params;
  const url = API + "/v1/" + path.join("/") + req.nextUrl.search;
  const upstream = await fetch(url, {
    method: "GET",
    headers: {
      Authorization: "Bearer " + BEARER,
      "X-Dev-User-Id": USER,
      Accept: "application/json",
    },
    cache: "no-store",
  });
  const body = await upstream.text();
  return new Response(body, {
    status: upstream.status,
    headers: { "Content-Type": upstream.headers.get("Content-Type") ?? "application/json" },
  });
}
