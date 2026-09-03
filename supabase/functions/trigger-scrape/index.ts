import "jsr:@supabase/functions-js/edge-runtime.d.ts";

const cors = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type, x-scrape-secret",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: cors });
  }

  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "Method not allowed" }), {
      status: 405,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  const secret = Deno.env.get("SCRAPE_SECRET");
  if (secret && req.headers.get("x-scrape-secret") !== secret) {
    return new Response(JSON.stringify({ error: "Unauthorized" }), {
      status: 401,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  const token = Deno.env.get("GITHUB_TOKEN");
  if (!token) {
    return new Response(JSON.stringify({ error: "GITHUB_TOKEN not configured" }), {
      status: 500,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  const repo = Deno.env.get("GITHUB_REPO") || "DreezyAL24/Kampala-Wakiso-apartment-screening-engine";
  const ref = Deno.env.get("GITHUB_REF") || "master";

  const gh = await fetch(`https://api.github.com/repos/${repo}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      event_type: "scrape-now",
      client_payload: { triggered_at: new Date().toISOString(), ref },
    }),
  });

  if (!gh.ok) {
    const text = await gh.text();
    return new Response(JSON.stringify({ error: "GitHub dispatch failed", details: text }), {
      status: 502,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  }

  return new Response(JSON.stringify({ ok: true, message: "Scrape started", ref }), {
    status: 200,
    headers: { ...cors, "Content-Type": "application/json" },
  });
});
