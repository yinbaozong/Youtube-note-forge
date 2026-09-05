const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const test = require("node:test");

function page({ id = "P2zRQ3BUu30", events } = {}) {
  const response = { videoDetails: { videoId: id, lengthSeconds: "100", title: "Video" },
    captions: { playerCaptionsTracklistRenderer: { captionTracks: [
      { languageCode: "en", baseUrl: "https://www.youtube.com/api/timedtext?v=" + id },
    ] } } };
  const context = vm.createContext({ URL, AbortSignal, setTimeout, Date, location: { href: "https://www.youtube.com/watch?v=P2zRQ3BUu30" },
    window: {}, document: { querySelector: selector => selector === "#movie_player" ? { getPlayerResponse: () => response } : null,
      querySelectorAll: () => [] },
    fetch: async () => ({ ok: true, status: 200, json: async () => ({ events }) }),
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../captions.js"), "utf8"), context);
  return context;
}

test("captures complete current-video captions without exporting signed URLs", async () => {
  const context = page({ events: [
    { tStartMs: 0, dDurationMs: 1000, segs: [{ utf8: "Opening" }] },
    { tStartMs: 95000, dDurationMs: 5000, segs: [{ utf8: "End" }] },
  ] });
  const result = await vm.runInContext('collectPageCaptions("P2zRQ3BUu30")', context);
  assert.equal(result.status, "ok");
  assert.equal(result.entries.length, 2);
  assert.ok(!JSON.stringify(result).includes("timedtext"));
});

test("rejects a stale player response from another video", async () => {
  const result = await vm.runInContext('collectPageCaptions("P2zRQ3BUu30")', page({ id: "previousVideo" }));
  assert.equal(result.status, "player_not_ready");
});

test("reads complete native model even when only visible rows are mounted", async () => {
  const context = page();
  context.document.querySelectorAll = selector => selector === "ytd-transcript-renderer" ? [{ data: { body: { segments: [
    { transcriptSegmentRenderer: { startMs: "0", endMs: "1000", snippet: { runs: [{ text: "Opening" }] } } },
    { transcriptSegmentRenderer: { startMs: "95000", endMs: "100000", snippet: { runs: [{ text: "End" }] } } },
  ] } } }] : [];
  const result = await vm.runInContext('collectPageCaptions("P2zRQ3BUu30")', context);
  assert.equal(result.status, "ok");
  assert.equal(result.entries.length, 2);
});

test("duplicate transcript panels produce one ordered transcript", async () => {
  const context = page();
  const row = (stamp, text) => ({ querySelector: selector => ({ textContent: selector === ".segment-timestamp" ? stamp : text }) });
  context.document.querySelectorAll = () => [row("0:00", "Opening"), row("1:35", "End"), row("0:00", "Opening"), row("1:35", "End")];
  const result = await vm.runInContext('collectPageCaptions("P2zRQ3BUu30")', context);
  assert.equal(result.status, "ok");
  assert.equal(result.entries.length, 2);
  assert.equal(result.entries[1].start, 95);
});
