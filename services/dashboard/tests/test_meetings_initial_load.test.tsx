// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Meeting } from "@/types/vexa";

const { routerPush, transcriptSearch } = vi.hoisted(() => ({
  routerPush: vi.fn(),
  transcriptSearch: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: routerPush }),
}));
vi.mock("sonner", () => ({ toast: { error: vi.fn() } }));
vi.mock("@/hooks/use-pending-meeting", () => ({ usePendingMeeting: vi.fn() }));
vi.mock("@/hooks/use-runtime-config", () => ({
  useRuntimeConfig: () => ({ config: null }),
}));
vi.mock("@/stores/join-modal-store", () => ({
  useJoinModalStore: (selector: (state: { openModal: () => void }) => unknown) =>
    selector({ openModal: vi.fn() }),
}));
vi.mock("@/components/docs/docs-link", () => ({ DocsLink: () => null }));
vi.mock("@/components/meetings/meeting-card", () => ({
  MeetingCard: ({ meeting }: { meeting: Meeting }) => <div>{meeting.id}</div>,
}));
vi.mock("@/components/meetings/transcript-search-results", () => ({
  TranscriptSearchResults: () => <div data-testid="transcript-results" />,
}));
vi.mock("@/lib/transcript-search", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/transcript-search")>();
  return { ...original, fetchTranscriptSearch: transcriptSearch };
});
vi.mock("@/components/ui/select", async () => {
  const React = await import("react");
  return {
    Select: ({
      value,
      onValueChange,
      children,
    }: {
      value: string;
      onValueChange: (value: string) => void;
      children: React.ReactNode;
    }) =>
      React.createElement(
        "select",
        { value, onChange: (event: React.ChangeEvent<HTMLSelectElement>) => onValueChange(event.target.value) },
        children
      ),
    SelectTrigger: () => null,
    SelectValue: () => null,
    SelectContent: ({ children }: { children: React.ReactNode }) =>
      React.createElement(React.Fragment, null, children),
    SelectItem: ({ value, children }: { value: string; children: React.ReactNode }) =>
      React.createElement("option", { value }, children),
  };
});

import MeetingsPage from "@/app/meetings/page";
import { vexaAPI } from "@/lib/api";
import { useMeetingsStore } from "@/stores/meetings-store";

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

let container: HTMLDivElement;
let root: Root;

async function renderPage() {
  await act(async () => {
    root.render(<MeetingsPage />);
    await Promise.resolve();
  });
}

function changeInput(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
  setter?.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

beforeEach(() => {
  vi.useFakeTimers();
  transcriptSearch.mockReset();
  transcriptSearch.mockResolvedValue([]);
  routerPush.mockReset();
  useMeetingsStore.setState(useMeetingsStore.getInitialState());
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      observe() {}
      disconnect() {}
    }
  );
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("meetings initial loading", () => {
  it("R01 one foreground fetch per production mount", async () => {
    const pending = deferred<{ meetings: Meeting[]; has_more: boolean }>();
    const getMeetings = vi.spyOn(vexaAPI, "getMeetings").mockReturnValue(pending.promise);

    await renderPage();
    expect(getMeetings).toHaveBeenCalledTimes(1);
    expect(getMeetings).toHaveBeenCalledWith({ limit: 50, offset: 0 });

    await act(async () => {
      pending.resolve({ meetings: [], has_more: false });
      await pending.promise;
    });
    expect(getMeetings).toHaveBeenCalledTimes(1);
  });

  it("R01 filters and search retain request values", async () => {
    const getMeetings = vi.spyOn(vexaAPI, "getMeetings").mockResolvedValue({
      meetings: [],
      has_more: false,
    });
    await renderPage();
    expect(getMeetings).toHaveBeenCalledTimes(1);

    const selects = container.querySelectorAll("select");
    await act(async () => {
      selects[1].value = "completed";
      selects[1].dispatchEvent(new Event("change", { bubbles: true }));
      await Promise.resolve();
    });
    expect(getMeetings).toHaveBeenCalledTimes(2);
    expect(getMeetings).toHaveBeenLastCalledWith({ limit: 50, offset: 0, status: "completed" });

    const input = container.querySelector("input") as HTMLInputElement;
    await act(async () => changeInput(input, "定例"));
    await act(async () => vi.advanceTimersByTimeAsync(299));
    expect(getMeetings).toHaveBeenCalledTimes(2);

    await act(async () => vi.advanceTimersByTimeAsync(1));
    expect(getMeetings).toHaveBeenCalledTimes(3);
    expect(getMeetings).toHaveBeenLastCalledWith({
      limit: 50,
      offset: 0,
      search: "定例",
      status: "completed",
    });
    expect(transcriptSearch).toHaveBeenCalledTimes(1);
    expect(transcriptSearch).toHaveBeenCalledWith("定例");
  });

  it("R01 unmount cancels pending search", async () => {
    const getMeetings = vi.spyOn(vexaAPI, "getMeetings").mockResolvedValue({
      meetings: [],
      has_more: false,
    });
    await renderPage();
    const input = container.querySelector("input") as HTMLInputElement;

    await act(async () => changeInput(input, "定例"));
    await act(async () => vi.advanceTimersByTimeAsync(100));
    await act(async () => root.unmount());
    await act(async () => vi.advanceTimersByTimeAsync(1000));

    expect(getMeetings).toHaveBeenCalledTimes(1);
    expect(transcriptSearch).not.toHaveBeenCalled();
  });
});
