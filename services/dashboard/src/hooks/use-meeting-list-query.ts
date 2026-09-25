"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { useMeetingsStore } from "@/stores/meetings-store";
import type { MeetingStatus, Platform } from "@/types/vexa";

const SEARCH_DEBOUNCE_MS = 300;

interface UseMeetingListQueryOptions {
  /** Called with the raw input after the search debounce elapses. */
  onDebouncedSearch?: (value: string) => void;
}

/**
 * Owns the meeting list query state (search / platform / status) and the
 * single effect that turns it into a fetch.
 *
 * There is exactly one effect that triggers `fetchMeetings`: the mount +
 * filter-change effect. The page used to have a second `useEffect(() =>
 * fetchMeetings(), [fetchMeetings])` on top of it, so every mount issued two
 * identical list requests.
 */
export function useMeetingListQuery(options: UseMeetingListQueryOptions = {}) {
  const { onDebouncedSearch } = options;
  const fetchMeetings = useMeetingsStore((state) => state.fetchMeetings);

  const [searchQuery, setSearchQuery] = useState("");
  const [platformFilter, setPlatformFilter] = useState<Platform | "all">("all");
  const [statusFilter, setStatusFilter] = useState<MeetingStatus | "all">("all");

  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const filtersRef = useRef({ search: "", status: "" as string, platform: "" as string });

  const applyFilters = useCallback((search: string, status: string, platform: string) => {
    filtersRef.current = { search, status, platform };
    fetchMeetings({
      search: search || undefined,
      status: status === "all" ? undefined : status,
      platform: platform === "all" ? undefined : platform,
    });
  }, [fetchMeetings]);

  // Mount + dropdown filter changes. The only fetch trigger in this hook
  // besides the debounced search and the explicit refresh.
  useEffect(() => {
    applyFilters(searchQuery, statusFilter, platformFilter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [statusFilter, platformFilter]);

  useEffect(() => {
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  const handleSearchChange = useCallback((value: string) => {
    setSearchQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      applyFilters(value, statusFilter, platformFilter);
      onDebouncedSearch?.(value);
    }, SEARCH_DEBOUNCE_MS);
  }, [applyFilters, statusFilter, platformFilter, onDebouncedSearch]);

  const refresh = useCallback(() => {
    applyFilters(searchQuery, statusFilter, platformFilter);
  }, [applyFilters, searchQuery, statusFilter, platformFilter]);

  return {
    searchQuery,
    setSearch: handleSearchChange,
    platformFilter,
    setPlatformFilter,
    statusFilter,
    setStatusFilter,
    refresh,
  };
}
