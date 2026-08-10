"use client"; import type { FocusEvent, RefObject } from "react"; import { Loader2, X } from "lucide-react"; import { Button } from "@/components/ui/button"; import { Textarea } from "@/components/ui/textarea";
type Props = { isNotesExpanded: boolean; setIsNotesExpanded: (value: boolean) => void; isSavingNotes: boolean; setIsEditingNotes: (value: boolean) => void; notesTextareaRef: RefObject<HTMLTextAreaElement | null>; editedNotes: string; setEditedNotes: (value: string) => void; handleNotesFocus: (event: FocusEvent<HTMLTextAreaElement>) => void; handleNotesBlur: () => void; isChatgptPromptExpanded: boolean; setIsChatgptPromptExpanded: (value: boolean) => void; chatgptPromptTextareaRef: RefObject<HTMLTextAreaElement | null>; editedChatgptPrompt: string; setEditedChatgptPrompt: (value: string) => void; handleChatgptPromptBlur: () => void; chatgptPrompt: string; };
export function MeetingDetailMobileOverlays(props: Props) { const { isNotesExpanded, setIsNotesExpanded, isSavingNotes, setIsEditingNotes, notesTextareaRef, editedNotes, setEditedNotes, handleNotesFocus, handleNotesBlur, isChatgptPromptExpanded, setIsChatgptPromptExpanded, chatgptPromptTextareaRef, editedChatgptPrompt, setEditedChatgptPrompt, handleChatgptPromptBlur, chatgptPrompt } = props; return <>
{/* Collapsible Notes Section - Mobile Only */}
{isNotesExpanded && (
  <div className="lg:hidden sticky top-0 z-50 bg-card text-card-foreground rounded-lg border shadow-sm overflow-hidden animate-in slide-in-from-top-2 duration-200">
    <div className="p-3 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">メモ</span>
        <div className="flex items-center gap-2">
          {isSavingNotes && (
            <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2 className="h-3 w-3 animate-spin" />
              保存中...
            </div>
          )}
          <Button
            variant="ghost"
            size="sm"
            className="h-6 w-6 p-0"
            onClick={() => {
              setIsNotesExpanded(false);
              setIsEditingNotes(false);
            }}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>
      <Textarea
        ref={notesTextareaRef}
        value={editedNotes}
        onChange={(e) => setEditedNotes(e.target.value)}
        onFocus={handleNotesFocus}
        onBlur={handleNotesBlur}
        placeholder="この会議のメモを追加..."
        className="min-h-[120px] resize-none text-sm"
        disabled={isSavingNotes}
        autoFocus
      />
    </div>
  </div>
)}

{/* Collapsible AI Prompt Section - Mobile Only */}
{isChatgptPromptExpanded && (
  <div className="lg:hidden sticky top-0 z-50 bg-card text-card-foreground rounded-lg border shadow-sm overflow-hidden animate-in slide-in-from-top-2 duration-200">
    <div className="p-3 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">AIプロンプト</span>
        <Button
          variant="ghost"
          size="sm"
          className="h-6 w-6 p-0"
          onClick={() => {
            setIsChatgptPromptExpanded(false);
          }}
        >
          <X className="h-4 w-4" />
        </Button>
      </div>
      <div className="space-y-2">
        <Textarea
          ref={chatgptPromptTextareaRef}
          value={editedChatgptPrompt}
          onChange={(e) => setEditedChatgptPrompt(e.target.value)}
          onBlur={handleChatgptPromptBlur}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              setEditedChatgptPrompt(chatgptPrompt);
              setIsChatgptPromptExpanded(false);
            }
          }}
          placeholder="AIプロンプト（文字起こしURLには {url} を使います）"
          className="min-h-[120px] resize-none text-sm"
          autoFocus
        />
        <p className="text-xs text-muted-foreground">
          文字起こしURLの差し込み位置として <code className="px-1 py-0.5 bg-muted rounded">{"{url}"}</code> を使います。
        </p>
      </div>
    </div>
  </div>
)}
</>; }
