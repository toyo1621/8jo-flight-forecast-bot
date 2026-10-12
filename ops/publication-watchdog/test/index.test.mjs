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
      return Response.json({ workflow_runs: runs.map((run) => ({ head_branch: "main", ...run })) });
    }
    return dispatchStatus === 200
      ? Response.json({ workflow_run_id: 987 }, { status: 200 })
      : new Response(null, { status: dispatchStatus });
  };
  return { calls, fetcher };
}

test("cron covers hourly daytime and three-hour nighttime checks in JST", () => {
  const config = JSON.parse(readFileSync(new URL("../wrangler.jsonc", import.meta.url)));
  const utcHours = config.triggers.crons.flatMap((expression) => {
    const [minute, hours] = expression.split(" ");
    assert.equal(minute, "45");
    return hours.split(",").flatMap((part) => {
      const [start, end = start] = part.split("-").map(Number);
      return Array.from({ length: end - start + 1 }, (_, index) => start + index);
    });
  });
  const jstHours = utcHours.map((hour) => (hour + 9) % 24).sort((a, b) => a - b);
  assert.deepEqual(jstHours, [0, 3, ...Array.from({ length: 17 }, (_, index) => index + 6)]);
});

test("freshness threshold follows JST daytime, including boundaries", () => {
  assert.equal(staleThresholdMs(new Date("2026-09-29T21:00:00Z")), 2.5 * HOUR);
  assert.equal(staleThresholdMs(new Date("2026-09-30T13:59:00Z")), 2.5 * HOUR);
  assert.equal(staleThresholdMs(new Date("2026-09-30T14:00:00Z")), 4.5 * HOUR);
});

test("daytime refresh runs before the stale threshold when an hourly run is missing", async () => {
  const { calls, fetcher } = fakeFetch({ publishedAt: new Date(now - HOUR).toISOString() });
  const result = await checkPublication({ GITHUB_ACTIONS_TOKEN: "test" }, { now, fetcher });
  assert.equal(result.status, "dispatched");
  assert.equal(result.reason, "hourly-refresh");
  assert.equal(calls.length, 3);
});

test("recent scheduled or manual runs suppress duplicate daytime refreshes", async () => {
  for (const event of ["schedule", "workflow_dispatch", "push"]) {
    const { calls, fetcher } = fakeFetch({
      publishedAt: new Date(now - 30 * 60 * 1000).toISOString(),
      runs: [{ status: "completed", event, created_at: new Date(now - 30 * 60 * 1000).toISOString() }],
    });
    const result = await checkPublication({ GITHUB_ACTIONS_TOKEN: "test" }, { now, fetcher });
    assert.equal(result.status, "cooldown");
    assert.equal(calls.length, 2);
  }
});

test("daytime cooldown permits the next hour regardless of build duration", async () => {
  for (const ageMinutes of [55, 60]) {
    const { calls, fetcher } = fakeFetch({
      publishedAt: new Date(now - 45 * 60 * 1000).toISOString(),
      runs: [{
        status: "completed",
        event: "workflow_dispatch",
        created_at: new Date(now - ageMinutes * 60 * 1000).toISOString(),
      }],
    });
    const result = await checkPublication({ GITHUB_ACTIONS_TOKEN: "test" }, { now, fetcher });
    assert.equal(result.status, "dispatched");
    assert.equal(calls.length, 3);
  }
});

test("daytime cooldown keeps the 55-minute boundary", async () => {
  const { calls, fetcher } = fakeFetch({
    publishedAt: new Date(now - 3 * HOUR).toISOString(),
    runs: [{ status: "completed", event: "schedule", created_at: new Date(now - 55 * 60 * 1000 + 1).toISOString() }],
  });
  const result = await checkPublication({ GITHUB_ACTIONS_TOKEN: "test" }, { now, fetcher });
  assert.equal(result.status, "cooldown");
  assert.equal(calls.length, 2);
});

test("fresh nighttime manifest never calls GitHub", async () => {
  const night = new Date("2026-09-30T18:45:00Z"); // 03:45 JST
  const { calls, fetcher } = fakeFetch({ publishedAt: new Date(night - 4 * HOUR).toISOString() });
  const result = await checkPublication({}, { now: night, fetcher });
  assert.equal(result.status, "fresh");
  assert.equal(calls.length, 1);
});

test("nighttime recovery retains the three-and-a-half-hour manual cooldown", async () => {
  const night = new Date("2026-09-30T18:45:00Z");
  for (const ageHours of [3, 3.5]) {
    const { calls, fetcher } = fakeFetch({
      publishedAt: new Date(night - 5 * HOUR).toISOString(),
      runs: [{ status: "completed", event: "workflow_dispatch", created_at: new Date(night - ageHours * HOUR).toISOString() }],
    });
    const result = await checkPublication({ GITHUB_ACTIONS_TOKEN: "test" }, { now: night, fetcher });
    assert.equal(result.status, ageHours < 3.5 ? "cooldown" : "dispatched");
    assert.equal(calls.length, ageHours < 3.5 ? 2 : 3);
    if (result.status === "dispatched") assert.equal(result.reason, "stale-recovery");
  }
});

test("hourly refresh waits for every active Pages status", async () => {
  for (const status of ["queued", "in_progress", "pending", "requested", "waiting"]) {
    const { calls, fetcher } = fakeFetch({
      publishedAt: new Date(now - 30 * 60 * 1000).toISOString(),
      runs: [{ status, event: "schedule" }],
    });
    const result = await checkPublication({ GITHUB_ACTIONS_TOKEN: "test" }, { now, fetcher });
    assert.equal(result.status, "running");
    assert.equal(calls.length, 2);
  }
});

test("hourly refresh starts and ends at the JST daytime boundaries", async () => {
  for (const [timestamp, expected] of [
    ["2026-09-29T20:59:00Z", "fresh"],
    ["2026-09-29T21:00:00Z", "dispatched"],
    ["2026-09-30T13:59:00Z", "dispatched"],
    ["2026-09-30T14:00:00Z", "fresh"],
  ]) {
    const boundary = new Date(timestamp);
    const { fetcher } = fakeFetch({ publishedAt: new Date(boundary - HOUR).toISOString() });
    const result = await checkPublication({ GITHUB_ACTIONS_TOKEN: "test" }, { now: boundary, fetcher });
    assert.equal(result.status, expected);
  }
});

test("recent manual dispatch suppresses repeated recovery", async () => {
  const { calls, fetcher } = fakeFetch({
    publishedAt: new Date(now - 3 * HOUR).toISOString(),
    runs: [{
      status: "completed",
      event: "workflow_dispatch",
      created_at: new Date(now - HOUR / 2).toISOString(),
    }],
  });
  const result = await checkPublication({ GITHUB_ACTIONS_TOKEN: "test" }, { now, fetcher });
  assert.equal(result.status, "cooldown");
  assert.equal(calls.length, 2);
});

test("a run on another branch does not suppress main recovery", async () => {
  const { calls, fetcher } = fakeFetch({
    publishedAt: new Date(now - 3 * HOUR).toISOString(),
    runs: [{ status: "in_progress", event: "workflow_dispatch", head_branch: "feature" }],
  });
  const result = await checkPublication({ GITHUB_ACTIONS_TOKEN: "test" }, { now, fetcher });
  assert.equal(result.status, "dispatched");
  assert.equal(calls.length, 3);
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

test("manifest fetch, JSON and timestamp errors stop hourly dispatch", async () => {
  for (const response of [
    () => new Response("unavailable", { status: 503 }),
    () => new Response("{invalid"),
    () => Response.json({}),
    () => Response.json(manifest("invalid+09:00")),
    () => Response.json(manifest(new Date(now.getTime() + 6 * 60 * 1000).toISOString())),
  ]) {
    let calls = 0;
    const fetcher = async () => {
      calls += 1;
      return response();
    };
    await assert.rejects(checkPublication({ GITHUB_ACTIONS_TOKEN: "test" }, { now, fetcher }));
    assert.equal(calls, 1);
  }
});

test("GitHub runs errors cannot trigger an hourly dispatch", async () => {
  for (const response of [
    () => new Response("unavailable", { status: 503 }),
    () => new Response("{invalid"),
    () => Response.json({}),
  ]) {
    let calls = 0;
    const fetcher = async () => {
      calls += 1;
      return calls === 1 ? Response.json(manifest(now.toISOString())) : response();
    };
    await assert.rejects(checkPublication({ GITHUB_ACTIONS_TOKEN: "test" }, { now, fetcher }));
    assert.equal(calls, 2);
  }
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
