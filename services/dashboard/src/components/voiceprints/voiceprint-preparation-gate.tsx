import type { ReactNode, RefObject } from "react";
import { Loader2 } from "lucide-react";

interface VoiceprintPreparationGateProps {
  active: boolean;
  overlayRef: RefObject<HTMLDivElement | null>;
  children: ReactNode;
}

export function VoiceprintPreparationGate({
  active,
  overlayRef,
  children,
}: VoiceprintPreparationGateProps) {
  return (
    <>
      {active && (
        <div
          ref={overlayRef}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-background/80 backdrop-blur-sm"
          role="dialog"
          aria-modal="true"
          aria-live="polite"
          aria-label="確認再生を準備中"
          tabIndex={-1}
        >
          <div className="flex items-center gap-3 rounded-lg border bg-card px-5 py-4 shadow-lg">
            <Loader2 className="h-5 w-5 animate-spin" />
            <span className="font-medium">確認再生を準備しています...</span>
          </div>
        </div>
      )}
      <div
        className="space-y-6"
        inert={active ? true : undefined}
        aria-hidden={active ? true : undefined}
      >
        {children}
      </div>
    </>
  );
}
