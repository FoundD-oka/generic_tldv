"use client";

import Image from "next/image";
import Link from "next/link";
import type {} from "react";
import { ArrowLeft, Check, ChevronDown, ClipboardCopy, Download, FileJson, FileText, Loader2, Monitor, Pencil, Share, Sparkles, StopCircle, X } from "lucide-react";
import { toast } from "sonner";
import { AIChatPanel } from "@/components/ai";
import { DocsLink } from "@/components/docs/docs-link";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge"; import { Button } from "@/components/ui/button"; import { Input } from "@/components/ui/input";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { exportToTxt } from "@/lib/export"; import { beginMeetingTitleEdit, customDesktopTitle, desktopMeetingTitle } from "@/lib/meeting-detail-title"; import { withBasePath } from "@/lib/base-path"; import { cn } from "@/lib/utils";
import type { Meeting, TranscriptSegment } from "@/types/vexa"; import { MeetingExportItems } from "@/hooks/use-meeting-actions";

type Props = { currentMeeting: Meeting; transcripts: TranscriptSegment[]; statusConfig: { label: string; color: string; bgColor: string }; hasBrowserView: boolean; viewMode: "transcript" | "browser"; setViewMode: (mode: "transcript" | "browser") => void; isEditingTitle: boolean; setIsEditingTitle: (value: boolean) => void; editedTitle: string; setEditedTitle: (value: string) => void; isSavingTitle: boolean; handleSaveTitle: () => Promise<void>; handleOpenInProvider: (provider: "chatgpt" | "perplexity") => void; handleExport: (format: "txt" | "json" | "srt" | "vtt") => void; hasRecordingAudio: boolean; handleDownloadRecordingAudio: (format: "webm" | "mp3") => void; isDownloadingRecording: boolean; recordingDownloadTarget: unknown; isStoppingBot: boolean; handleStopBot: () => void; apiViewOpen: boolean; apiBaseUrl: string; authToken: string | null; };
export function MeetingDetailDesktopHeader(props: Props) { const { currentMeeting, transcripts, statusConfig, hasBrowserView, viewMode, setViewMode, isEditingTitle, setIsEditingTitle, editedTitle, setEditedTitle, isSavingTitle, handleSaveTitle, handleOpenInProvider, handleExport, hasRecordingAudio, handleDownloadRecordingAudio, isDownloadingRecording, recordingDownloadTarget, isStoppingBot, handleStopBot, apiViewOpen, apiBaseUrl, authToken } = props;
 return (
<div className="hidden lg:flex items-center justify-between gap-4 mb-6">
  <div className="flex items-center gap-4 flex-1 min-w-0">
    <Button variant="ghost" size="sm" asChild className="-ml-2 h-8 px-2 text-muted-foreground hover:text-foreground">
      <Link href="/meetings">
        <ArrowLeft className="h-4 w-4" />
      </Link>
    </Button>

    {isEditingTitle ? (
      <div className="flex items-center gap-2 flex-1 max-w-md">
        <div className="flex items-center gap-2 flex-1">
          <Input
            value={editedTitle}
            onChange={(e) => setEditedTitle(e.target.value)}
            className="text-xl font-bold h-9"
            placeholder="会議タイトル..."
            autoFocus
            disabled={isSavingTitle}
          onKeyDown={async (e) => {
            if (e.key === "Enter" && editedTitle.trim()) {
              await handleSaveTitle();
            } else if (e.key === "Escape") {
              setIsEditingTitle(false);
            }
          }}
        />
        <div className="flex items-center gap-1">
          <Button
            size="icon"
            variant="ghost"
            className="h-8 w-8 text-green-600"
            disabled={isSavingTitle || !editedTitle.trim()}
            onClick={async () => {
              if (!editedTitle.trim()) return;
              await handleSaveTitle();
            }}
          >
            {isSavingTitle ? <Loader2 className="h-4 w-4 animate-spin" /> : <Check className="h-4 w-4" />}
          </Button>
          <Button
            size="icon"
            variant="ghost"
            className="h-8 w-8 text-muted-foreground"
            disabled={isSavingTitle}
            onClick={() => setIsEditingTitle(false)}
          >
            <X className="h-4 w-4" />
          </Button>
          <DocsLink href="/docs/cookbook/rename-meeting" />
        </div>
        </div>
      </div>
    ) : (
      <div className="flex items-center gap-3 min-w-0">
        <div className="flex items-center gap-2 group min-w-0">
          <h1 className="text-xl font-bold tracking-tight truncate">
            {desktopMeetingTitle(currentMeeting)}
          </h1>
          <Button
            size="icon"
            variant="ghost"
            className="h-7 w-7 opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
            onClick={() => {
              beginMeetingTitleEdit(currentMeeting, setEditedTitle, customDesktopTitle);
              setIsEditingTitle(true);
            }}
          >
            <Pencil className="h-3.5 w-3.5" />
          </Button>
        </div>
        <Badge className={cn("shrink-0", statusConfig.bgColor, statusConfig.color)}>
          {statusConfig.label}
        </Badge>
      </div>
    )}
  </div>

  <div className="flex items-center gap-2 shrink-0">
    {hasBrowserView && (
      <div className="flex items-center border rounded-md overflow-hidden bg-background shadow-sm h-9">
        <Button
          variant="ghost"
          size="sm"
          className={cn("rounded-r-none h-full gap-1.5", viewMode === 'transcript' && "bg-muted")}
          onClick={() => setViewMode('transcript')}
        >
          <FileText className="h-4 w-4" />
          文字起こし
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className={cn("rounded-l-none h-full gap-1.5", viewMode === 'browser' && "bg-muted")}
          onClick={() => setViewMode('browser')}
        >
          <Monitor className="h-4 w-4" />
          ブラウザ
        </Button>
      </div>
    )}
    {(currentMeeting.status === "active" || currentMeeting.status === "completed" || currentMeeting.status === "failed") && transcripts.length > 0 && (
      <div className="flex items-center gap-2">
        <AIChatPanel
          meeting={currentMeeting}
          transcripts={transcripts}
          trigger={
            <Button className="gap-2 h-9">
              <Sparkles className="h-4 w-4" />
              AIに質問
            </Button>
          }
        />

        <div className="flex items-center gap-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="outline"
                className="gap-2 h-9"
                title="エクスポート"
              >
                <Share className="h-4 w-4" />
                <span>エクスポート</span>
                <ChevronDown className="h-4 w-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => handleOpenInProvider("chatgpt")}>
                <Image src={withBasePath("/icons/icons8-chatgpt-100.png")} alt="ChatGPT" width={16} height={16} unoptimized className="object-contain mr-2 invert dark:invert-0" />
                ChatGPTで開く
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => handleOpenInProvider("perplexity")}>
                <Image src={withBasePath("/icons/icons8-perplexity-ai-100.png")} alt="Perplexity" width={16} height={16} unoptimized className="object-contain mr-2" />
                Perplexityで開く
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => handleExport("txt")}>
                <FileText className="h-4 w-4 mr-2" />
                .txtをダウンロード
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => handleExport("json")}>
                <FileJson className="h-4 w-4 mr-2" />
                .jsonをダウンロード
              </DropdownMenuItem>
              <MeetingExportItems handleExport={handleExport} variant="desktop" />
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => {
                  if (!currentMeeting || transcripts.length === 0) return;
                  const text = exportToTxt(currentMeeting, transcripts);
                  navigator.clipboard.writeText(text).then(() => {
                    toast.success("文字起こしをクリップボードにコピーしました");
                  });
                }}
                disabled={transcripts.length === 0}
              >
                <ClipboardCopy className="h-4 w-4 mr-2" />
                クリップボードにコピー
              </DropdownMenuItem>
		                    {hasRecordingAudio && (
		                      <>
		                        <DropdownMenuItem
		                          onClick={() => handleDownloadRecordingAudio("webm")}
		                          disabled={isDownloadingRecording || !recordingDownloadTarget}
		                        >
		                          <Download className="h-4 w-4 mr-2" />
		                          {isDownloadingRecording ? "音声を準備中" : "WebMをダウンロード"}
		                        </DropdownMenuItem>
		                        <DropdownMenuItem
		                          onClick={() => handleDownloadRecordingAudio("mp3")}
		                          disabled={isDownloadingRecording || !recordingDownloadTarget}
		                        >
		                          <Download className="h-4 w-4 mr-2" />
		                          MP3をダウンロード
		                        </DropdownMenuItem>
		                      </>
		                    )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </div>
    )}
    {currentMeeting.status === "active" && (
      <div className="flex items-center">
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button
              variant="outline"
              className="gap-2 text-destructive hover:text-destructive hover:bg-destructive/10 h-9"
              disabled={isStoppingBot}
            >
              {isStoppingBot ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <StopCircle className="h-4 w-4" />
              )}
              停止
            </Button>
          </AlertDialogTrigger>
        <AlertDialogContent className={apiViewOpen ? "sm:max-w-lg" : undefined}>
          <AlertDialogHeader>
            <AlertDialogTitle>文字起こしを停止しますか？</AlertDialogTitle>
            <AlertDialogDescription>
              ボットを会議から退出させ、ライブ文字起こしを停止します。停止後も文字起こしは確認できます。
            </AlertDialogDescription>
          </AlertDialogHeader>
          {apiViewOpen && currentMeeting && (
            <div className="rounded-lg overflow-hidden border border-border bg-[#111111] font-mono text-[11px]">
              <div className="px-3 py-2 bg-[#1a1a1a] flex items-center justify-between">
                <div className="flex items-center gap-[5px]">
                  <span className="w-2 h-2 rounded-full bg-[#ff5f57]" />
                  <span className="w-2 h-2 rounded-full bg-[#febc2e]" />
                  <span className="w-2 h-2 rounded-full bg-[#28c840]" />
                </div>
                <span className="text-[10px] text-gray-500">DELETE /bots</span>
              </div>
              <div className="p-3 leading-relaxed">
                <div className="text-gray-500 mb-2"># ボットを停止</div>
                <div>
                  <span className="text-gray-300">curl -X </span>
                  <span className="text-[#fca5a5]">DELETE</span>
                  <span className="text-gray-300"> \</span>
                </div>
                <div className="pl-4">
                  <span className="text-[#6ee7b7]">{apiBaseUrl}/bots/{currentMeeting.platform}/{currentMeeting.platform_specific_id}</span>
                  <span className="text-gray-300"> \</span>
                </div>
                <div className="pl-4">
                  <span className="text-gray-300">-H </span>
                  <span className="text-[#7dd3fc]">&apos;X-API-Key: {authToken ? `${authToken.slice(0, 8)}...` : "vx_sk_..."}&apos;</span>
                </div>
              </div>
            </div>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel>キャンセル</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleStopBot}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              文字起こしを停止
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <DocsLink href="/docs/rest/bots#stop-bot" />
      </div>
    )}

    {/* Agent and API buttons hidden for now */}

  </div>
</div>
); }
