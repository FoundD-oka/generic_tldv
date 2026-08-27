// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * 一覧の fetch トリガは「mount + フィルタ変更」の1本だけであることを固定する。
 * 以前は mount effect と filter effect が二重に走り、初回表示で同じ一覧を
 * 2回取得していた。
 */

const fetchMeetings = vi.fn();

vi.mock("@/stores/meetings-store", () => ({
  useMeetingsStore: (selector: (state: { fetchMeetings: typeof fetchMeetings }) => unknown) =>
    selector({ fetchMeetings }),
}));

import { useMeetingListQuery } from "@/hooks/use-meeting-list-query";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

type HookApi = ReturnType<typeof useMeetingListQuery>;

let api: HookApi;
let container: HTMLDivElement;
let root: Root;

function Harness({ onSearch }: { onSearch?: (value: string) => void }) {
  api = useMeetingListQuery({ onDebouncedSearch: onSearch });
  return null;
}

function mount(onSearch?: (value: string) => void) {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  act(() => {
    root.render(<Harness onSearch={onSearch} />);
  });
}

describe("useMeetingListQuery", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    fetchMeetings.mockClear();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
  });

  it("mount では一覧を1回だけ取得する", () => {
    mount();

    expect(fetchMeetings).toHaveBeenCalledTimes(1);
    expect(fetchMeetings).toHaveBeenCalledWith({
      search: undefined,
      status: undefined,
      platform: undefined,
    });
  });

  it("status / platform の変更でそれぞれ1回ずつ再取得する", () => {
    mount();

    act(() => api.setStatusFilter("completed"));
    expect(fetchMeetings).toHaveBeenCalledTimes(2);
    expect(fetchMeetings).toHaveBeenLastCalledWith({
      search: undefined,
      status: "completed",
      platform: undefined,
    });

    act(() => api.setPlatformFilter("teams"));
    expect(fetchMeetings).toHaveBeenCalledTimes(3);
    expect(fetchMeetings).toHaveBeenLastCalledWith({
      search: undefined,
      status: "completed",
      platform: "teams",
    });
  });

  it("検索入力は 300ms 後に1回だけ取得する", () => {
    const onSearch = vi.fn();
    mount(onSearch);

    act(() => api.setSearch("週"));
    act(() => api.setSearch("週次"));
    act(() => api.setSearch("週次定例"));
    expect(fetchMeetings).toHaveBeenCalledTimes(1); // mount の1回のみ

    act(() => { vi.advanceTimersByTime(300); });

    expect(fetchMeetings).toHaveBeenCalledTimes(2);
    expect(fetchMeetings).toHaveBeenLastCalledWith({
      search: "週次定例",
      status: undefined,
      platform: undefined,
    });
    expect(onSearch).toHaveBeenCalledTimes(1);
    expect(onSearch).toHaveBeenCalledWith("週次定例");
  });

  it("refresh は現在のフィルタで1回取得する", () => {
    mount();

    act(() => api.setStatusFilter("failed"));
    act(() => api.refresh());

    expect(fetchMeetings).toHaveBeenCalledTimes(3);
    expect(fetchMeetings).toHaveBeenLastCalledWith({
      search: undefined,
      status: "failed",
      platform: undefined,
    });
  });
});
