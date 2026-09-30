const MANIFEST_URL =
  "https://toyo1621.github.io/8jo-flight-forecast-bot/build-manifest.json";
const RUNS_URL =
  "https://api.github.com/repos/toyo1621/8jo-flight-forecast-bot/actions/workflows/pages.yml/runs?branch=main&per_page=20";
const DISPATCH_URL =
  "https://api.github.com/repos/toyo1621/8jo-flight-forecast-bot/actions/workflows/pages.yml/dispatches";
const ACTIVE_STATUSES = new Set([
  "queued",
  "in_progress",
  "pending",
  "requested",
  "waiting",
]);
const DISPATCH_COOLDOWN_MS = 3.5 * 60 * 60 * 1000;

function jstHour(now) {
  return (now.getUTCHours() + 9) % 24;
}

export function staleThresholdMs(now) {
  const hour = jstHour(now);
  return (hour >= 6 && hour < 23 ? 2.5 : 4.5) * 60 * 60 * 1000;
}

async function getJson(fetcher, url, headers = {}) {
  const response = await fetcher(url, {
    headers,
    cache: "no-store",
    signal: AbortSignal.timeout(8000),
  });
  if (!response.ok) {
    throw new Error(`GET ${new URL(url).pathname} returned HTTP ${response.status}`);
  }
  return response.json();
}

function parseGeneratedAt(manifest, now) {
  const value = manifest?.generated_at;
  if (typeof value !== "string" || !/(Z|[+-]\d{2}:\d{2})$/.test(value)) {
    throw new Error("Published manifest has no timezone-aware generated_at");
  }
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp) || timestamp > now.getTime() + 5 * 60 * 1000) {
    throw new Error("Published manifest has an invalid generated_at");
  }
  return timestamp;
}

function githubHeaders(token) {
  return {
    Accept: "application/vnd.github+json",
    "User-Agent": "8jo-flight-publication-watchdog",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

export async function checkPublication(env, { now = new Date(), fetcher = fetch } = {}) {
  const manifest = await getJson(fetcher, MANIFEST_URL);
  const generatedAt = parseGeneratedAt(manifest, now);
  const ageMs = now.getTime() - generatedAt;
  const thresholdMs = staleThresholdMs(now);
  if (ageMs <= thresholdMs) {
    return { status: "fresh", ageMs, runId: manifest.run_id ?? null };
  }

  const runs = await getJson(fetcher, RUNS_URL, githubHeaders(env.GITHUB_ACTIONS_TOKEN));
  if (!Array.isArray(runs?.workflow_runs)) {
    throw new Error("GitHub workflow runs response is invalid");
  }
  if (runs.workflow_runs.some((run) => ACTIVE_STATUSES.has(run.status))) {
    return { status: "running", ageMs, runId: manifest.run_id ?? null };
  }
  const recentDispatch = runs.workflow_runs.some((run) => {
    const createdAt = Date.parse(run.created_at);
    return (
      run.event === "workflow_dispatch" &&
      Number.isFinite(createdAt) &&
      createdAt <= now.getTime() &&
      now.getTime() - createdAt < DISPATCH_COOLDOWN_MS
    );
  });
  if (recentDispatch) {
    return { status: "cooldown", ageMs, runId: manifest.run_id ?? null };
  }
  if (!env.GITHUB_ACTIONS_TOKEN) {
    throw new Error("GITHUB_ACTIONS_TOKEN is not configured");
  }

  const response = await fetcher(DISPATCH_URL, {
    method: "POST",
    headers: {
      ...githubHeaders(env.GITHUB_ACTIONS_TOKEN),
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref: "main", inputs: { full_quality_checks: false } }),
    signal: AbortSignal.timeout(8000),
  });
  if (response.status !== 200 && response.status !== 204) {
    throw new Error(`GitHub workflow dispatch returned HTTP ${response.status}`);
  }
  const dispatch = response.status === 200 ? await response.json() : null;
  return {
    status: "dispatched",
    ageMs,
    runId: manifest.run_id ?? null,
    dispatchedRunId: dispatch?.workflow_run_id ?? null,
  };
}

export default {
  async scheduled(_controller, env) {
    const result = await checkPublication(env);
    console.log(JSON.stringify(result));
  },
};
