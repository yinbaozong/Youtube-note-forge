import type { PipelineResult } from "./types";

const RESULT_MARKER = "PIPELINE_RESULT=";

export function parsePipelineResults(output: string): PipelineResult[] {
  const results: PipelineResult[] = [];
  for (const line of output.split(/\r?\n/)) {
    const markerAt = line.indexOf(RESULT_MARKER);
    if (markerAt < 0) continue;
    const candidate = line.slice(markerAt + RESULT_MARKER.length).trim();
    try {
      const parsed: unknown = JSON.parse(candidate);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        results.push(parsed as PipelineResult);
      }
    } catch {
      // Human-readable output and partial lines are deliberately ignored.
    }
  }
  return results;
}

export function lastPipelineResult(output: string): PipelineResult | undefined {
  return parsePipelineResults(output).at(-1);
}
