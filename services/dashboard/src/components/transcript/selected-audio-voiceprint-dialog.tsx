"use client";

import { useMemo, useState } from "react";
import { AlertCircle, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { VoiceprintSegmentsPreview } from "@/lib/api";

interface SelectedAudioVoiceprintDialogProps {
  open: boolean;
  selectedCount: number;
  selectedDurationSeconds: number;
  preview: VoiceprintSegmentsPreview | null;
  previewing: boolean;
  previewError: string | null;
  submitting: boolean;
  onOpenChange: (open: boolean) => void;
  onRetryPreview: () => void;
  onSubmit: (displayName: string) => Promise<void>;
}

export function canSubmitSelectedAudioVoiceprint(
  displayName: string,
  audioReviewConfirmed: boolean,
  consentConfirmed: boolean,
  hasPreview: boolean,
  submitting: boolean
): boolean {
  return (
    displayName.trim().length > 0
    && audioReviewConfirmed
    && consentConfirmed
    && hasPreview
    && !submitting
  );
}

function formatSeconds(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0.0秒";
  return `${seconds.toFixed(1)}秒`;
}

export function SelectedAudioVoiceprintDialog({
  open,
  selectedCount,
  selectedDurationSeconds,
  preview,
  previewing,
  previewError,
  submitting,
  onOpenChange,
  onRetryPreview,
  onSubmit,
}: SelectedAudioVoiceprintDialogProps) {
  const [displayName, setDisplayName] = useState("");
  const [audioReviewConfirmed, setAudioReviewConfirmed] = useState(false);
  const [consentConfirmed, setConsentConfirmed] = useState(false);

  const audioDataUrl = useMemo(
    () => preview
      ? `data:${preview.content_type};base64,${preview.audio_base64}`
      : null,
    [preview]
  );
  const canSubmit = canSubmitSelectedAudioVoiceprint(
    displayName,
    audioReviewConfirmed,
    consentConfirmed,
    !!preview,
    submitting
  );

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!submitting) onOpenChange(nextOpen);
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>選択した音声から声紋を登録</DialogTitle>
          <DialogDescription>
            選択した音声だけを使います。表示中の話者名や、同じ話者グループの
            他の発話は自動では含めません。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="rounded-md border bg-muted/30 p-3 text-sm">
            <p className="font-medium">
              {selectedCount}件・合計{formatSeconds(selectedDurationSeconds)}を選択中
            </p>
            {preview && (
              <p className="mt-1 text-xs text-muted-foreground">
                登録に使用する確認音声: {preview.selection_count}件・{formatSeconds(preview.duration_seconds)}
              </p>
            )}
          </div>

          {previewing && (
            <div className="flex items-center gap-2 rounded-md border p-3 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              選択した区間から確認音声を作成しています
            </div>
          )}

          {previewError && !previewing && (
            <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm">
              <div className="flex items-start gap-2 text-destructive">
                <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>{previewError}</span>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="mt-3 gap-1.5"
                onClick={onRetryPreview}
              >
                <RefreshCw className="h-3.5 w-3.5" />
                確認音声を再作成
              </Button>
            </div>
          )}

          {audioDataUrl && (
            <div className="space-y-2">
              <Label>登録前の確認再生</Label>
              <audio
                key={`${preview?.source_fingerprint}:${preview?.clip_sha256}`}
                className="w-full"
                controls
                preload="metadata"
                src={audioDataUrl}
                aria-label="声紋登録に使用する選択音声の確認再生"
              />
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="selected-audio-voiceprint-display-name">話者名</Label>
            <Input
              id="selected-audio-voiceprint-display-name"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder="例: 田中"
              maxLength={255}
              autoFocus
              disabled={submitting}
            />
          </div>

          <label className="flex items-start gap-2 rounded-md border p-3 text-sm">
            <input
              type="checkbox"
              checked={audioReviewConfirmed}
              onChange={(event) => setAudioReviewConfirmed(event.target.checked)}
              disabled={!preview || submitting}
              className="mt-0.5 h-4 w-4 accent-primary"
            />
            <span>
              確認音声を再生し、すべて同じ本人の声だけで、他の人の声が
              混ざっていないことを確認しました。
            </span>
          </label>

          <label className="flex items-start gap-2 rounded-md border p-3 text-sm">
            <input
              type="checkbox"
              checked={consentConfirmed}
              onChange={(event) => setConsentConfirmed(event.target.checked)}
              disabled={!preview || submitting}
              className="mt-0.5 h-4 w-4 accent-primary"
            />
            <span>
              本人から、今後の会議で話者候補を提示するための声紋登録に
              同意を得ています。声紋は暗号化して保存されます。
            </span>
          </label>
        </div>

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitting}
          >
            キャンセル
          </Button>
          <Button
            type="button"
            disabled={!canSubmit}
            onClick={() => void onSubmit(displayName.trim())}
          >
            {submitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {submitting ? "登録中" : "確認した音声で登録"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
