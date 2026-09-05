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
