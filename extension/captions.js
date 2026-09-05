// Runs in the current YouTube page; no cookies or signed caption URLs leave it.
async function collectPageCaptions(expectedId) {
  const deadline = Date.now() + 14000;
  const currentId = () => new URL(location.href).searchParams.get("v") || location.pathname.split("/")[2];
  if (currentId() !== expectedId) return { status: "video_mismatch" };
  const player = document.querySelector("#movie_player");
  const response = player?.getPlayerResponse?.() || window.ytInitialPlayerResponse || {};
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
    if (currentId() !== expectedId || !entries.length) return null;
    const last = entries[entries.length - 1];
    // Never treat the currently visible player caption or a truncated panel as a transcript.
    if (!Number.isFinite(duration) || duration <= 0 || entries[0].start > 90 ||
        (last.end || last.start) < duration * 0.7) return null;
    return { status: "ok", video_id: expectedId, metadata, language, source, entries };
  }
  function panelTranscript() {
    const rows = [...document.querySelectorAll("ytd-transcript-segment-renderer")];
    const entries = rows.map(row => {
      const stamp = row.querySelector(".segment-timestamp")?.textContent?.trim() || "";
      const start = stamp.split(":").reduce((n, s) => n * 60 + Number(s), 0);
      return { start, text: row.querySelector(".segment-text")?.textContent?.trim() || "" };
    });
    return finish(entries, "und", "browser:youtube-transcript");
  }
  const existing = panelTranscript();
  if (existing) return existing;
  const tracks = response.captions?.playerCaptionsTracklistRenderer?.captionTracks || [];
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
  return { status: "unavailable", diagnostic, video_id: expectedId };
}

if (typeof module !== "undefined") module.exports = { collectPageCaptions };
