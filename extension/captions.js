// Runs in the current YouTube page; no cookies or signed caption URLs leave it.
async function collectPageCaptions(expectedId) {
  const deadline = Date.now() + 14000;
  const currentId = () => new URL(location.href).searchParams.get("v") || location.pathname.split("/")[2];
  if (currentId() !== expectedId) return { status: "video_mismatch" };
  const player = document.querySelector("#movie_player");
  const liveResponse = player?.getPlayerResponse?.();
  const response = liveResponse?.videoDetails?.videoId === expectedId ? liveResponse : window.ytInitialPlayerResponse || {};
  if (response.videoDetails?.videoId !== expectedId) return { status: "player_not_ready" };
  const duration = Number(response.videoDetails.lengthSeconds);
  const metadata = {
    id: expectedId, title: response.videoDetails.title, duration,
    channel: response.videoDetails.author,
    thumbnail: `https://i.ytimg.com/vi/${expectedId}/hqdefault.jpg`,
    webpage_url: `https://www.youtube.com/watch?v=${expectedId}`,
  };
  function finish(entries, language, source) {
    entries = entries.filter(e => Number.isFinite(e.start) && e.start >= 0 && e.text?.trim());
    // YouTube may mount the same transcript in multiple panels, including hidden ones.
    const seen = new Set();
    entries = entries.sort((a, b) => a.start - b.start).filter(e => {
      const key = JSON.stringify([e.start, e.end ?? null, e.text.trim()]);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    if (currentId() !== expectedId || !entries.length) return null;
    const last = entries[entries.length - 1];
    // Never treat the currently visible player caption or a truncated panel as a transcript.
    if (!Number.isFinite(duration) || duration <= 0 || entries[0].start > 90 ||
        (last.end || last.start) < duration * 0.7) return null;
    return { status: "ok", video_id: expectedId, metadata, language, source, entries };
  }
  function panelTranscript() {
    // Read the complete native panel model when only a viewport of rows is mounted.
    for (const panel of document.querySelectorAll("ytd-transcript-renderer")) {
      const entries = [];
      const visit = (node, depth = 0) => {
        if (!node || typeof node !== "object" || depth > 18) return;
        const segment = node.transcriptSegmentRenderer;
        if (segment) entries.push({ start: Number(segment.startMs) / 1000,
          end: Number(segment.endMs) / 1000,
          text: (segment.snippet?.runs || []).map(run => run.text || "").join("") || segment.snippet?.simpleText || "" });
        for (const value of Object.values(node)) visit(value, depth + 1);
      };
      visit(panel.data);
      const ready = finish(entries, "und", "browser:youtube-transcript");
      if (ready) return ready;
    }
    const rows = [...document.querySelectorAll("ytd-transcript-segment-renderer, ytd-transcript-segment-list-renderer [role=listitem]")];
    const entries = rows.map(row => {
      const stamp = (row.querySelector(".segment-timestamp") || row.querySelector("[class*=timestamp]"))?.textContent?.trim() || "";
      const start = stamp.split(":").reduce((n, s) => n * 60 + Number(s), 0);
      return { start, text: (row.querySelector(".segment-text") || row.querySelector("yt-formatted-string[class*=segment]"))?.textContent?.trim() || "" };
    });
    return finish(entries, "und", "browser:youtube-transcript");
  }
  const existing = panelTranscript();
  if (existing) return existing;
  let tracks = response.captions?.playerCaptionsTracklistRenderer?.captionTracks || [];
  if (!tracks.length) {
    const refreshed = player?.getPlayerResponse?.() || {};
    tracks = refreshed.captions?.playerCaptionsTracklistRenderer?.captionTracks || [];
  }
  const original = tracks.find(t => t.languageCode === response.microformat?.playerMicroformatRenderer?.defaultAudioLanguage)
    || tracks.find(t => t.kind === "asr") || tracks[0];
  let diagnostic = tracks.length ? "caption_download_failed" : "tracks_not_exposed";
  if (original?.baseUrl) {
    try {
      const url = new URL(original.baseUrl);
      if (url.protocol !== "https:" || !/(^|\.)youtube\.com$/.test(url.hostname)) throw new Error("caption_host_invalid");
      url.searchParams.set("fmt", "json3");
      const result = await fetch(url, { credentials: "include", signal: AbortSignal.timeout(5000) });
      diagnostic = `caption_http_${result.status}`;
      if (result.ok) {
        const payload = await result.json();
        const entries = (payload.events || []).filter(e => e.segs).map(e => ({
          start: e.tStartMs / 1000, end: (e.tStartMs + (e.dDurationMs || 0)) / 1000,
          text: e.segs.map(s => s.utf8 || "").join("").trim(),
        }));
        const ready = finish(entries, original.languageCode, "browser:youtube-captions");
        if (ready) return ready;
      }
    } catch { /* The native transcript panel uses YouTube's own request context. */ }
  }
  document.querySelector("ytd-watch-metadata #description-inline-expander #expand")?.click();
  let clicked = false;
  while (Date.now() < deadline && currentId() === expectedId) {
    if (!clicked) {
      const button = document.querySelector("ytd-video-description-transcript-section-renderer button");
      if (button) { button.click(); clicked = true; }
    }
    const ready = panelTranscript();
    if (ready) return ready;
    await new Promise(resolve => setTimeout(resolve, 250));
  }
  return { status: clicked ? "transcript_incomplete" : "page_not_ready", diagnostic, video_id: expectedId,
    entry_count: document.querySelectorAll("ytd-transcript-segment-renderer").length };
}

if (typeof module !== "undefined") module.exports = { collectPageCaptions };
