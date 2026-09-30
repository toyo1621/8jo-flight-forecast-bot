import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { checkPublication, staleThresholdMs } from "../src/index.mjs";

const HOUR = 60 * 60 * 1000;
const now = new Date("2026-09-30T06:45:00Z"); // 15:45 JST
const manifest = (generatedAt) => ({
  generated_at: generatedAt,
  run_id: "12345",
});

function fakeFetch({ publishedAt, runs = [], dispatchStatus = 204 } = {}) {
  const calls = [];
  const fetcher = async (url, options) => {
    calls.push({ url, options });
    if (url.endsWith("build-manifest.json")) {
      return Response.json(manifest(publishedAt));
    }
    if (url.includes("/runs?")) {
      return Response.json({ workflow_runs: runs });
    }
    return dispatchStatus === 200
      ? Response.json({ workflow_run_id: 987 }, { status: 200 })
      : new Response(null, { status: dispatchStatus });
  };
  return { calls, fetcher };
}

test("cron covers two-hour daytime and three-hour nighttime checks in JST", () => {
  const config = JSON.parse(readFileSync(new URL("../wrangler.jsonc", import.meta.url)));
  const utcHours = config.triggers.crons.flatMap((expression) => {
    const [minute, hours] = expression.split(" ");
    assert.equal(minute, "45");
    return hours.split(",").map(Number);
  });
  const jstHours = utcHours.map((hour) => (hour + 9) % 24).sort((a, b) => a - b);
  assert.deepEqual(jstHours, [0, 3, 6, 8, 10, 12, 14, 16, 18, 20, 22]);
});

test("freshness threshold follows JST daytime, including boundaries", () => {
  assert.equal(staleThresholdMs(new Date("2026-09-29T21:00:00Z")), 2.5 * HOUR);
  assert.equal(staleThresholdMs(new Date("2026-09-30T13:59:00Z")), 2.5 * HOUR);
  assert.equal(staleThresholdMs(new Date("2026-09-30T14:00:00Z")), 4.5 * HOUR);
});

test("fresh manifest never calls GitHub", async () => {
  const { calls, fetcher } = fakeFetch({ publishedAt: new Date(now - 2 * HOUR).toISOString() });
  const result = await checkPublication({}, { now, fetcher });
  assert.equal(result.status, "fresh");
  assert.equal(calls.length, 1);
});

test("stale manifest waits for active Pages run", async () => {
  const { calls, fetcher } = fakeFetch({
    publishedAt: new Date(now - 3 * HOUR).toISOString(),
    runs: [{ status: "in_progress", event: "schedule" }],
  });
  const result = await checkPublication({ GITHUB_ACTIONS_TOKEN: "test" }, { now, fetcher });
  assert.equal(result.status, "running");
  assert.equal(calls.length, 2);
});

test("recent manual dispatch suppresses repeated recovery", async () => {
  const { calls, fetcher } = fakeFetch({
    publishedAt: new Date(now - 3 * HOUR).toISOString(),
    runs: [{
      status: "completed",
      event: "workflow_dispatch",
      created_at: new Date(now - HOUR).toISOString(),
    }],
  });
  const result = await checkPublication({ GITHUB_ACTIONS_TOKEN: "test" }, { now, fetcher });
  assert.equal(result.status, "cooldown");
  assert.equal(calls.length, 2);
});

test("stale manifest dispatches lightweight Pages workflow after CI gate", async () => {
  const { calls, fetcher } = fakeFetch({ publishedAt: new Date(now - 3 * HOUR).toISOString() });
  const result = await checkPublication({ GITHUB_ACTIONS_TOKEN: "test" }, { now, fetcher });
  assert.equal(result.status, "dispatched");
  assert.equal(calls.length, 3);
  assert.equal(calls[2].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[2].options.body), {
    ref: "main", inputs: { full_quality_checks: false },
  });
});

test("invalid manifest and missing token cannot trigger a dispatch", async () => {
  const bad = fakeFetch({ publishedAt: "2026-09-30T04:00:00" });
  await assert.rejects(checkPublication({}, { now, fetcher: bad.fetcher }), /generated_at/);
  assert.equal(bad.calls.length, 1);

  const stale = fakeFetch({ publishedAt: new Date(now - 3 * HOUR).toISOString() });
  await assert.rejects(checkPublication({}, { now, fetcher: stale.fetcher }), /GITHUB_ACTIONS_TOKEN/);
  assert.equal(stale.calls.length, 2);
});

test("GitHub dispatch failure is reported, not treated as success", async () => {
  const { fetcher } = fakeFetch({
    publishedAt: new Date(now - 3 * HOUR).toISOString(),
    dispatchStatus: 403,
  });
  await assert.rejects(
    checkPublication({ GITHUB_ACTIONS_TOKEN: "test" }, { now, fetcher }),
    /HTTP 403/,
  );
});

test("GitHub dispatch response with run ID is accepted", async () => {
  const { fetcher } = fakeFetch({
    publishedAt: new Date(now - 3 * HOUR).toISOString(),
    dispatchStatus: 200,
  });
  const result = await checkPublication({ GITHUB_ACTIONS_TOKEN: "test" }, { now, fetcher });
  assert.equal(result.status, "dispatched");
  assert.equal(result.dispatchedRunId, 987);
});
