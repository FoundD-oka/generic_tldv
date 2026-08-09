import { readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  MAX_TRANSCRIPT_SEARCH_SNIPPETS,
  buildTranscriptSearchUrl,
  escapeRegExpLiteral,
  fetchTranscriptSearch,
  highlightLiteralMatches,
  parseTranscriptSearchResponse,
  shouldSearchTranscripts,
} from "@/lib/transcript-search";
import { getDashboardCopy } from "@/lib/dashboard-copy";

const pageSource = readFileSync("src/app/meetings/page.tsx", "utf8");
const sectionSource = readFileSync(
  "src/components/meetings/transcript-search-results.tsx",
  "utf8",
);

describe("文字起こし横断検索の呼び出しロジック", () => {
  afterEach(() => vi.restoreAllMocks());

  it("strip後2文字以上のときだけ検索を発行する", () => {
    expect(shouldSearchTranscripts("")).toBe(false);
    expect(shouldSearchTranscripts("   ")).toBe(false);
    expect(shouldSearchTranscripts("あ")).toBe(false);
    expect(shouldSearchTranscripts(" あ ")).toBe(false);
    expect(shouldSearchTranscripts("会議")).toBe(true);
    expect(shouldSearchTranscripts("  会議  ")).toBe(true);
  });

  it("検索URLは汎用プロキシ配下でクエリをエンコードする", () => {
    expect(buildTranscriptSearchUrl(" 議事録 ")).toBe(
      "/api/vexa/transcripts/search?q=%E8%AD%B0%E4%BA%8B%E9%8C%B2",
    );
  });

  it("fetchTranscriptSearchはプロキシへGETし、整形済みの結果を返す", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        JSON.stringify({
          query: "議事録",
          results: [
            {
              meeting: { id: 12, title: "定例会議", native_meeting_id: "abc-defg-hij" },
              match_count: 7,
              matches: [{ speaker: "田中", text: "議事録を共有します" }],
            },
          ],
          has_more: false,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      ),
    );

    const hits = await fetchTranscriptSearch("議事録");

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      "/api/vexa/transcripts/search?q=%E8%AD%B0%E4%BA%8B%E9%8C%B2",
    );
    expect(hits).toEqual([
      {
        meetingId: "12",
        title: "定例会議",
        matchCount: 7,
        snippets: [{ speaker: "田中", text: "議事録を共有します" }],
      },
    ]);
  });

  it("エラー応答は例外にする(UIのエラー表示へつなぐ)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response("", { status: 500 }));
    await expect(fetchTranscriptSearch("議事録")).rejects.toThrow(/500/);
  });
});

describe("検索レスポンスの整形", () => {
  it("会議あたりのスニペットを最大3件に切り詰め、総マッチ数を保つ", () => {
    const hits = parseTranscriptSearchResponse({
      results: [
        {
          meeting: { id: 3, title: "設計レビュー" },
          match_count: 9,
          matches: [
            { speaker: "A", text: "one" },
            { speaker: "B", text: "two" },
            { speaker: null, text: "three" },
            { speaker: "D", text: "four" },
          ],
        },
      ],
    });

    expect(MAX_TRANSCRIPT_SEARCH_SNIPPETS).toBe(3);
    expect(hits[0].snippets).toHaveLength(3);
    expect(hits[0].snippets[2]).toEqual({ speaker: null, text: "three" });
    expect(hits[0].matchCount).toBe(9);
  });

  it("タイトルが無い会議は会議IDフォールバック、それも無ければ無題表示にする", () => {
    const hits = parseTranscriptSearchResponse({
      results: [
        { meeting: { id: 1, title: null, native_meeting_id: "abc-defg-hij" }, matches: [] },
        { meeting: { id: 2, title: null, native_meeting_id: null }, matches: [] },
      ],
    });

    expect(hits.map((hit) => hit.title)).toEqual(["abc-defg-hij", "無題の会議"]);
  });

  it("結果が無い・壊れた応答でも空配列にする", () => {
    expect(parseTranscriptSearchResponse({ results: [] })).toEqual([]);
    expect(parseTranscriptSearchResponse({})).toEqual([]);
    expect(parseTranscriptSearchResponse(null)).toEqual([]);
    expect(parseTranscriptSearchResponse({ results: [{ meeting: {} }] })).toEqual([]);
  });
});

describe("スニペットのリテラル強調", () => {
  it("一致部分だけをmatchとして切り出す", () => {
    expect(highlightLiteralMatches("今日の議事録を共有します", "議事録")).toEqual([
      { text: "今日の", match: false },
      { text: "議事録", match: true },
      { text: "を共有します", match: false },
    ]);
  });

  it("大文字小文字を区別しない(API側のILIKEに合わせる)", () => {
    expect(highlightLiteralMatches("Kabosu と kabosu", "kabosu")).toEqual([
      { text: "Kabosu", match: true },
      { text: " と ", match: false },
      { text: "kabosu", match: true },
    ]);
  });

  it("正規表現メタ文字を含むクエリはリテラルとして扱う", () => {
    expect(escapeRegExpLiteral("a.*b")).toBe("a\\.\\*b");
    expect(highlightLiteralMatches("axxb と a.*b", "a.*b")).toEqual([
      { text: "axxb と ", match: false },
      { text: "a.*b", match: true },
    ]);
    expect(highlightLiteralMatches("費用(概算)は未定", "(概算)")).toEqual([
      { text: "費用", match: false },
      { text: "(概算)", match: true },
      { text: "は未定", match: false },
    ]);
    expect(() => highlightLiteralMatches("未対応の [", "[")).not.toThrow();
    expect(highlightLiteralMatches("未対応の [", "[")).toEqual([
      { text: "未対応の ", match: false },
      { text: "[", match: true },
    ]);
  });

  it("一致が無い場合と空クエリの場合は全体を非強調で返す", () => {
    expect(highlightLiteralMatches("議事録", "予算")).toEqual([
      { text: "議事録", match: false },
    ]);
    expect(highlightLiteralMatches("議事録", "  ")).toEqual([
      { text: "議事録", match: false },
    ]);
  });
});

describe("会議一覧ページの配線", () => {
  it("既存の300msデバウンスでタイトル検索に加えて文字起こし検索を発行する", () => {
    expect(pageSource).toContain("applyFilters(value, statusFilter, platformFilter);\n      runTranscriptSearch(value);");
    expect(pageSource).toContain("}, 300);");
  });

  it("2文字未満ではセクションを出さない", () => {
    expect(pageSource).toContain("if (!shouldSearchTranscripts(value)) {");
    expect(pageSource).toContain("setTranscriptSearch(IDLE_TRANSCRIPT_SEARCH);");
    expect(pageSource).toContain('{transcriptSearch.status !== "idle" && (');
  });

  it("ローディング・エラー・完了の状態をセクションへ渡す", () => {
    expect(pageSource).toContain('setTranscriptSearch({ status: "loading", query, hits: [] });');
    expect(pageSource).toContain('setTranscriptSearch({ status: "ready", query, hits });');
    expect(pageSource).toContain('setTranscriptSearch({ status: "error", query, hits: [] });');
  });

  it("既存の一覧絞り込み(タイトル検索・status・platform)は従来どおり", () => {
    expect(pageSource).toContain("fetchMeetings({\n      search: search || undefined,");
    expect(pageSource).toContain('status: status === "all" ? undefined : status,');
    expect(pageSource).toContain('platform: platform === "all" ? undefined : platform,');
    expect(pageSource).toContain("const filteredMeetings = meetings;");
  });
});

describe("文字起こし一致セクションの表示", () => {
  it("会議詳細への既存導線・総マッチ数・スニペットを表示する", () => {
    expect(sectionSource).toContain("href={`/meetings/${hit.meetingId}`}");
    expect(sectionSource).toContain('copy.matchCount.replace("{count}", String(hit.matchCount))');
    expect(sectionSource).toContain("hit.snippets.map(");
  });

  it("一致部分を<mark>で強調する", () => {
    expect(sectionSource).toContain("highlightLiteralMatches(text, query)");
    expect(sectionSource).toContain("<mark");
  });

  it("ローディング・0件・エラーの各状態を日本語コピーで出す", () => {
    expect(sectionSource).toContain("{copy.loading}");
    expect(sectionSource).toContain("{copy.error}");
    expect(sectionSource).toContain("{copy.empty}");
    expect(sectionSource).toContain("{copy.title}");
  });
});

describe("新設コピーの日本語限定", () => {
  const locales = ["ja", "en"] as const;

  it("ja/en どちらのロケールでも日本語文言を返す", () => {
    for (const locale of locales) {
      const copy = getDashboardCopy(locale).meetings.transcriptSearch;
      expect(copy.title).toBe("文字起こしに一致");
      expect(copy.loading).toBe("文字起こしを検索中...");
      expect(copy.error).toBe("文字起こしの検索に失敗しました");
      expect(copy.empty).toBe("文字起こしに一致する会議はありません");
      expect(copy.matchCount).toBe("{count}件一致");
    }
  });

  it("プレースホルダを除いた本文にラテン文字を含まない", () => {
    for (const locale of locales) {
      const copy = getDashboardCopy(locale).meetings.transcriptSearch;
      for (const value of Object.values(copy)) {
        expect(value.replace(/\{[^}]*\}/g, "")).not.toMatch(/[A-Za-z]/);
      }
    }
  });
});
