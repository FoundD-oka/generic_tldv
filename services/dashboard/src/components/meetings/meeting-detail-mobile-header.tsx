"use client";
import Image from "next/image"; import Link from "next/link"; import { ArrowLeft, Check, ChevronDown, ClipboardCopy, Download, FileJson, FileText, Loader2, Monitor, Pencil, Share, StopCircle } from "lucide-react"; import { toast } from "sonner";
import { DocsLink } from "@/components/docs/docs-link"; import { LanguagePicker } from "@/components/language-picker";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from "@/components/ui/alert-dialog"; import { Badge } from "@/components/ui/badge"; import { Button } from "@/components/ui/button"; import { Input } from "@/components/ui/input"; import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { exportToTxt } from "@/lib/export"; import { beginMeetingTitleEdit, customMobileTitle, mobileMeetingTitle } from "@/lib/meeting-detail-title"; import { withBasePath } from "@/lib/base-path"; import { cn } from "@/lib/utils"; import type { Meeting, TranscriptSegment } from "@/types/vexa"; import { MeetingExportItems } from "@/hooks/use-meeting-actions";
type Props = { currentMeeting: Meeting; transcripts: TranscriptSegment[]; statusConfig: { label: string; color: string; bgColor: string }; hasBrowserView: boolean; viewMode: "transcript" | "browser"; setViewMode: (mode: "transcript" | "browser") => void; currentLanguage?: string; handleLanguageChange: (value: string) => void; isUpdatingConfig: boolean; isEditingTitle: boolean; setIsEditingTitle: (value: boolean) => void; editedTitle: string; setEditedTitle: (value: string) => void; isSavingTitle: boolean; handleSaveTitle: () => Promise<void>; setIsNotesExpanded: (value: boolean) => void; setIsEditingNotes: (value: boolean) => void; setEditedNotes: (value: string) => void; handleOpenInProvider: (provider: "chatgpt" | "perplexity") => void; handleExport: (format: "txt" | "json" | "srt" | "vtt") => void; hasRecordingAudio: boolean; handleDownloadRecordingAudio: (format: "webm" | "mp3") => void; isDownloadingRecording: boolean; recordingDownloadTarget: unknown; isStoppingBot: boolean; handleStopBot: () => void; };
export function MeetingDetailMobileHeader(props: Props) { const { currentMeeting, transcripts, statusConfig, hasBrowserView, viewMode, setViewMode, currentLanguage, handleLanguageChange, isUpdatingConfig, isEditingTitle, setIsEditingTitle, editedTitle, setEditedTitle, isSavingTitle, handleSaveTitle, setIsNotesExpanded, setIsEditingNotes, setEditedNotes, handleOpenInProvider, handleExport, hasRecordingAudio, handleDownloadRecordingAudio, isDownloadingRecording, recordingDownloadTarget, isStoppingBot, handleStopBot } = props;
 return (
<div className="lg:hidden sticky top-[-16px] z-40 bg-background/80 backdrop-blur-sm -mx-4 px-4 py-2 mb-2">
  <div
    className={cn(
      "bg-card text-card-foreground rounded-lg border shadow-sm px-2 py-1.5",
      "backdrop-blur supports-[backdrop-filter]:bg-card/95"
    )}
  >
    {/* Single Highly Compact Row for Mobile */}
    <div className="flex items-center gap-1">
      <Button variant="ghost" size="icon" className="h-7 w-7 -ml-0.5 shrink-0" asChild>
        <Link href="/meetings">
          <ArrowLeft className="h-3.5 w-3.5" />
        </Link>
      </Button>

      {/* Title & Platform Icon */}
      <div className="flex-1 min-w-0 flex items-center gap-1">
        {isEditingTitle ? (
          <div className="flex items-center gap-1 flex-1 min-w-0">
            <Input
              value={editedTitle}
              onChange={(e) => setEditedTitle(e.target.value)}
              className="text-[11px] font-medium h-6 flex-1 min-w-0 py-0 px-1.5"
              placeholder="タイトル..."
              autoFocus
              disabled={isSavingTitle}
              onBlur={() => {
                if (!isSavingTitle) setIsEditingTitle(false);
              }}
              onKeyDown={async (e) => {
                if (e.key === "Enter" && editedTitle.trim()) {
                  await handleSaveTitle();
                } else if (e.key === "Escape") {
                  setIsEditingTitle(false);
                }
              }}
            />
            <Button
              size="icon"
              variant="ghost"
              className="h-6 w-6 text-green-600 shrink-0"
              disabled={isSavingTitle || !editedTitle.trim()}
              onClick={async () => {
                if (!editedTitle.trim()) return;
                await handleSaveTitle();
              }}
            >
              {isSavingTitle ? <Loader2 className="h-3 w-3 animate-spin" /> : <Check className="h-3 w-3" />}
            </Button>
            <DocsLink href="/docs/cookbook/rename-meeting" />
          </div>
        ) : (
          <div
            className="flex items-center gap-1 group cursor-pointer min-w-0"
            onClick={() => {
              beginMeetingTitleEdit(currentMeeting, setEditedTitle, customMobileTitle);
              setIsEditingTitle(true);
            }}
          >
            <span className="text-xs font-semibold truncate">
              {mobileMeetingTitle(currentMeeting)}
            </span>
            <Pencil className="h-3 w-3 opacity-0 group-hover:opacity-100 transition-opacity shrink-0 text-muted-foreground" />
          </div>
        )}
      </div>

      {/* Status & Actions */}
      <div className="flex items-center gap-1 shrink-0">
        <Badge className={cn("text-[9px] h-4 px-1 shrink-0", statusConfig.bgColor, statusConfig.color)}>
          {statusConfig.label}
        </Badge>

        {/* Browser view toggle - Mobile */}
        {hasBrowserView && (
          <Button
            variant="ghost"
            size="icon"
            className={cn("h-7 w-7", viewMode === 'browser' && "bg-muted")}
            onClick={() => setViewMode(viewMode === 'browser' ? 'transcript' : 'browser')}
            title={viewMode === 'browser' ? '文字起こしを表示' : 'ブラウザ画面を表示'}
          >
            <Monitor className="h-3.5 w-3.5" />
          </Button>
        )}

        {/* Language Selector - Mobile (only when active) */}
        {currentMeeting.status === "active" && (
          <div className="flex items-center gap-0.5 shrink-0 ml-0.5">
            <LanguagePicker
              value={currentLanguage ?? "auto"}
              onValueChange={handleLanguageChange}
              disabled={isUpdatingConfig}
              compact
            />
            {isUpdatingConfig && (
              <Loader2 className="h-2.5 w-2.5 animate-spin" />
            )}
          </div>
        )}

        <div className="flex items-center border-l ml-0.5 pl-0.5 gap-0">
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-muted-foreground"
            onClick={() => {
              setEditedNotes(currentMeeting.data?.notes || "");
              setIsEditingNotes(true);
              setIsNotesExpanded(true);
            }}
            title="メモ"
          >
            <FileText className="h-3.5 w-3.5" />
          </Button>

          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline" size="icon" className="h-7 w-7 ml-0.5" title="エクスポート">
                <Share className="h-3.5 w-3.5" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onClick={() => handleOpenInProvider("chatgpt")} disabled={transcripts.length === 0}>
                <Image src={withBasePath("/icons/icons8-chatgpt-100.png")} alt="ChatGPT" width={16} height={16} unoptimized className="object-contain mr-2 invert dark:invert-0" />
                ChatGPTで開く
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => handleOpenInProvider("perplexity")} disabled={transcripts.length === 0}>
                <Image src={withBasePath("/icons/icons8-perplexity-ai-100.png")} alt="Perplexity" width={16} height={16} unoptimized className="object-contain mr-2" />
                Perplexityで開く
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => handleExport("txt")} disabled={transcripts.length === 0}>
                <FileText className="h-4 w-4 mr-2" />
                .txtをダウンロード
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => handleExport("json")} disabled={transcripts.length === 0}>
                <FileJson className="h-4 w-4 mr-2" />
                .jsonをダウンロード
              </DropdownMenuItem>
              <MeetingExportItems handleExport={handleExport} variant="mobile" />
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

          {currentMeeting.status === "active" && (
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7 text-destructive ml-0.5"
                  disabled={isStoppingBot}
                  title="停止"
                >
                  {isStoppingBot ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <StopCircle className="h-4 w-4" />
                  )}
                </Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>文字起こしを停止しますか？</AlertDialogTitle>
                  <AlertDialogDescription>
                    ボットを退出させ、文字起こしを停止します。
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>キャンセル</AlertDialogCancel>
                  <AlertDialogAction
                    onClick={handleStopBot}
                    className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                  >
                    停止
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          )}
        </div>
      </div>
    </div>
  </div>
</div>
); }
