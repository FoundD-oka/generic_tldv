"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { Plus, RefreshCw, CreditCard, Video, Loader2, Search } from "lucide-react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ErrorState } from "@/components/ui/error-state";
import { useMeetingsStore } from "@/stores/meetings-store";
import { useJoinModalStore } from "@/stores/join-modal-store";
import type { Platform, MeetingStatus } from "@/types/vexa";
import { DocsLink } from "@/components/docs/docs-link";
import { MeetingCard } from "@/components/meetings/meeting-card";
import { getWebappUrl } from "@/lib/docs/webapp-url";
import { Input } from "@/components/ui/input";
import { usePendingMeeting } from "@/hooks/use-pending-meeting";
import { toast } from "sonner";
import { withBasePath } from "@/lib/base-path";
import { DEFAULT_DASHBOARD_BRAND } from "@/lib/dashboard-brand";
import { getDashboardCopy } from "@/lib/dashboard-copy";
import { useRuntimeConfig } from "@/hooks/use-runtime-config";
import { isRetranscriptionInProgress } from "@/lib/retranscription-status";
import { startSingleFlightPolling } from "@/lib/single-flight-polling";

export default function MeetingsPage() {
  usePendingMeeting();
  const router = useRouter();
  const { meetings, isLoadingMeetings, isLoadingMore, hasMore, fetchMeetings, fetchMoreMeetings, error, subscriptionRequired } = useMeetingsStore();
  const openJoinModal = useJoinModalStore((state) => state.openModal);
  const { config } = useRuntimeConfig();
  const brand = config?.brand || DEFAULT_DASHBOARD_BRAND;
  const copy = getDashboardCopy(brand.locale).meetings;

  const [searchQuery, setSearchQuery] = useState("");
  const [platformFilter, setPlatformFilter] = useState<Platform | "all">("all");
  const [statusFilter, setStatusFilter] = useState<MeetingStatus | "all">("all");
  const [isCreatingBrowser, setIsCreatingBrowser] = useState(false);

  // Debounced server-side search
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(null);
  const filtersRef = useRef({ search: "", status: "" as string, platform: "" as string });

  const applyFilters = useCallback((search: string, status: string, platform: string) => {
    filtersRef.current = { search, status, platform };
    fetchMeetings({
      search: search || undefined,
      status: status === "all" ? undefined : status,
      platform: platform === "all" ? undefined : platform,
    });
  }, [fetchMeetings]);

  async function handleStartBrowserSession() {
    setIsCreatingBrowser(true);
    try {
      const body: Record<string, string> = { mode: "browser_session" };
      // Read git workspace config from localStorage
      try {
        const git = JSON.parse(localStorage.getItem("vexa-browser-git") || "{}");
        if (git.repo && git.token) {
          body.workspaceGitRepo = git.repo;
          body.workspaceGitToken = git.token;
          body.workspaceGitBranch = git.branch || "main";
        }
      } catch {}
      const response = await fetch(withBasePath("/api/vexa/bots"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({ detail: "Failed" }));
        throw new Error(err.detail || "Failed to create session");
      }
      const meeting = await response.json();
      // Navigate to the session
      setTimeout(() => router.push(`/meetings/${meeting.id}`), 2000);
    } catch (error) {
      toast.error((error as Error).message);
      setIsCreatingBrowser(false);
    }
  }

  // Initial load
  useEffect(() => {
    fetchMeetings();
  }, [fetchMeetings]);

  // Re-fetch when dropdown filters change
  useEffect(() => {
    applyFilters(searchQuery, statusFilter, platformFilter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, platformFilter]);

  // Debounce search input (300ms)
  const handleSearchChange = useCallback((value: string) => {
    setSearchQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      applyFilters(value, statusFilter, platformFilter);
    }, 300);
  }, [applyFilters, statusFilter, platformFilter]);

  const filteredMeetings = meetings;
  const hasRetranscriptionInProgress = meetings.some((meeting) =>
    isRetranscriptionInProgress(meeting.data)
  );

  useEffect(() => {
    if (!hasRetranscriptionInProgress) return;
    return startSingleFlightPolling(
      () => fetchMeetings(undefined, { silent: true }),
      2500
    );
  }, [fetchMeetings, hasRetranscriptionInProgress]);

  // Infinite scroll
  const sentinelRef = useRef<HTMLDivElement>(null);
  const handleLoadMore = useCallback(() => {
    if (hasMore && !isLoadingMore && !isLoadingMeetings) {
      fetchMoreMeetings();
    }
  }, [hasMore, isLoadingMore, isLoadingMeetings, fetchMoreMeetings]);

  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) handleLoadMore(); },
      { rootMargin: "200px" }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [handleLoadMore]);

  const handleRefresh = () => applyFilters(searchQuery, statusFilter, platformFilter);

  const handleSubscribe = () => {
    window.open(`${getWebappUrl()}/pricing`, "_blank");
  };

  return (
    <div className="space-y-6">
      {subscriptionRequired && (
        <div className="rounded-xl border border-amber-200 dark:border-amber-800 bg-amber-50 dark:bg-amber-950 p-4 flex items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <CreditCard className="h-5 w-5 text-amber-600 dark:text-amber-400 flex-shrink-0" />
            <div>
              <p className="text-sm font-medium text-amber-800 dark:text-amber-200">{copy.subscriptionRequiredTitle}</p>
              <p className="text-xs text-amber-700 dark:text-amber-300">
                {copy.subscriptionRequiredMessage}
              </p>
            </div>
          </div>
          <Button onClick={handleSubscribe} size="sm" className="bg-amber-600 hover:bg-amber-700 text-white flex-shrink-0">
            {copy.viewPlans}
          </Button>
        </div>
      )}

      {/* Header */}
      <div className="sticky top-0 z-10 bg-background -mx-4 md:-mx-6 px-4 md:px-6 py-4 -mt-4 md:-mt-6 border-b border-border/50 space-y-4">
        {/* Top row: title + join button */}
        <div className="flex items-center justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <h1 className="text-2xl font-semibold text-foreground">{copy.title}</h1>
              <DocsLink href="/docs/rest/meetings#list-meetings" />
            </div>
            <p className="text-sm text-muted-foreground">
              {copy.description}
            </p>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
            <Button variant="outline" size="icon" onClick={handleRefresh} disabled={isLoadingMeetings}>
              <RefreshCw className={`h-4 w-4 ${isLoadingMeetings ? "animate-spin" : ""}`} />
            </Button>
            {!subscriptionRequired && (
              <div className="flex items-center">
                <Button onClick={openJoinModal}>
                  <Plus className="mr-2 h-4 w-4" />
                  <span className="hidden sm:inline">{copy.joinMeeting}</span>
                  <span className="sm:hidden">{copy.joinShort}</span>
                </Button>
                <DocsLink href="/docs/rest/bots#create-bot" />
              </div>
            )}
          </div>
        </div>
        {/* Filters row */}
        <div className="flex flex-col sm:flex-row sm:flex-wrap gap-2">
          <div className="relative flex-1 min-w-0 sm:min-w-[180px] sm:max-w-[240px]">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
            <Input
              placeholder={copy.searchPlaceholder}
              value={searchQuery}
              onChange={(e) => handleSearchChange(e.target.value)}
              className="w-full pl-8"
            />
          </div>
          <div className="flex gap-2 min-w-0">
            <Select value={platformFilter} onValueChange={(v) => setPlatformFilter(v as Platform | "all")}>
              <SelectTrigger className="flex-1 min-w-0 sm:w-[140px] lg:w-[150px]">
                <SelectValue placeholder={copy.allPlatforms} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{copy.allPlatforms}</SelectItem>
                <SelectItem value="google_meet">Google Meet</SelectItem>
                <SelectItem value="teams">Teams</SelectItem>
                <SelectItem value="zoom">Zoom</SelectItem>
                <SelectItem value="browser_session">ブラウザ</SelectItem>
              </SelectContent>
            </Select>
            <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as MeetingStatus | "all")}>
              <SelectTrigger className="flex-1 min-w-0 sm:w-[130px] lg:w-[150px]">
                <SelectValue placeholder={copy.allStatus} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{copy.allStatus}</SelectItem>
                <SelectItem value="active">{copy.statuses.active}</SelectItem>
                <SelectItem value="completed">{copy.statuses.completed}</SelectItem>
                <SelectItem value="failed">{copy.statuses.failed}</SelectItem>
                <SelectItem value="joining">{copy.statuses.joining}</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>

      {/* Meetings cards */}
      {error ? (
        <ErrorState error={error} onRetry={fetchMeetings} />
      ) : subscriptionRequired && meetings.length === 0 ? (
        <ErrorState
          type="subscription"
          title={copy.subscribeTitle}
          message={copy.subscribeMessage}
          actionLabel={copy.viewPlans}
          onAction={handleSubscribe}
        />
      ) : (
        <div>
          {isLoadingMeetings ? (
            <div className="flex min-h-48 items-center justify-center rounded-2xl border border-border/60 bg-card/40">
              <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
          ) : filteredMeetings.length === 0 ? (
            <div className="flex min-h-56 flex-col items-center justify-center gap-2 rounded-2xl border border-dashed border-border bg-card/40 px-5 text-center">
              <Video className="h-8 w-8 text-muted-foreground/50" />
              <p className="text-sm text-muted-foreground">
                {searchQuery.trim() || platformFilter !== "all" || statusFilter !== "all"
                  ? copy.noMatches
                  : copy.noMeetings}
              </p>
              {!searchQuery.trim() && platformFilter === "all" && statusFilter === "all" && !subscriptionRequired && (
                <Button onClick={openJoinModal} size="sm" variant="outline" className="mt-2">
                  <Plus className="mr-2 h-3.5 w-3.5" />
                  {copy.joinFirst}
                </Button>
              )}
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5">
              {filteredMeetings.map((meeting, index) => (
                <div
                  key={meeting.id}
                  className="animate-fade-in-up"
                  style={{ animationDelay: `${Math.min(index, 10) * 30}ms`, animationFillMode: "backwards" }}
                >
                  <MeetingCard meeting={meeting} />
                </div>
              ))}
            </div>
          )}
          {(hasMore || isLoadingMore) && (
            <div ref={sentinelRef} className="flex justify-center py-4">
              {isLoadingMore && <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />}
            </div>
          )}
        </div>
      )}

    </div>
  );
}
