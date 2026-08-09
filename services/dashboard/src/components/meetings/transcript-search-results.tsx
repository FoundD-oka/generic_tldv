"use client";

import Link from "next/link";
import { Loader2 } from "lucide-react";
import { highlightLiteralMatches, type TranscriptSearchHit } from "@/lib/transcript-search";
import type { DashboardCopy } from "@/lib/dashboard-copy";

export type TranscriptSearchStatus = "loading" | "ready" | "error";

interface TranscriptSearchResultsProps {
  query: string;
  status: TranscriptSearchStatus;
  hits: TranscriptSearchHit[];
  copy: DashboardCopy["meetings"]["transcriptSearch"];
}

function Snippet({ text, query }: { text: string; query: string }) {
  return (
    <>
      {highlightLiteralMatches(text, query).map((segment, index) =>
        segment.match ? (
          <mark key={index} className="rounded bg-amber-200 px-0.5 text-foreground dark:bg-amber-700/60">
            {segment.text}
          </mark>
        ) : (
          <span key={index}>{segment.text}</span>
        )
      )}
    </>
  );
}

export function TranscriptSearchResults({
  query,
  status,
  hits,
  copy,
}: TranscriptSearchResultsProps) {
  return (
    <section className="rounded-2xl border border-border/60 bg-card/40 p-4 space-y-3">
      <h2 className="text-sm font-semibold text-foreground">{copy.title}</h2>

      {status === "loading" ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          {copy.loading}
        </p>
      ) : status === "error" ? (
        <p className="text-sm text-destructive">{copy.error}</p>
      ) : hits.length === 0 ? (
        <p className="text-sm text-muted-foreground">{copy.empty}</p>
      ) : (
        <ul className="space-y-3">
          {hits.map((hit) => (
            <li key={hit.meetingId} className="space-y-1">
              <div className="flex items-baseline justify-between gap-2">
                <Link
                  href={`/meetings/${hit.meetingId}`}
                  className="truncate text-sm font-medium text-foreground hover:underline"
                >
                  {hit.title}
                </Link>
                <span className="flex-shrink-0 text-xs text-muted-foreground">
                  {copy.matchCount.replace("{count}", String(hit.matchCount))}
                </span>
              </div>
              <ul className="space-y-1">
                {hit.snippets.map((snippet, index) => (
                  <li key={index} className="text-xs text-muted-foreground">
                    {snippet.speaker && (
                      <span className="mr-1 font-medium text-foreground">{snippet.speaker}:</span>
                    )}
                    <Snippet text={snippet.text} query={query} />
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
