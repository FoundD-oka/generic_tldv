"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Shield, Lock, ArrowLeft, Loader2 } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useAdminAuthStore } from "@/stores/admin-auth-store";
import { AdminAuthModal } from "./admin-auth-modal";
import { withBasePath } from "@/lib/base-path";

interface AdminGuardProps {
  children: React.ReactNode;
}

export function AdminGuard({ children }: AdminGuardProps) {
  const router = useRouter();
  const { isAdminAuthenticated } = useAdminAuthStore();
  const [showAuthModal, setShowAuthModal] = useState(false);
  const [isChecking, setIsChecking] = useState(true);

  // Check server-side session on mount
  useEffect(() => {
    const checkSession = async () => {
      try {
        const response = await fetch(withBasePath("/api/auth/admin-verify"));
        const data = await response.json();

        if (!data.authenticated && isAdminAuthenticated) {
          // Local state says authenticated but server says no - clear local state
          useAdminAuthStore.getState().logout();
        }
      } catch (error) {
        console.error("Failed to check admin session:", error);
      } finally {
        setIsChecking(false);
      }
    };

    checkSession();
  }, [isAdminAuthenticated]);

  // Show loading while checking
  if (isChecking) {
    return (
      <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4">
        <Loader2 className="h-8 w-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground">管理者権限を確認しています...</p>
      </div>
    );
  }

  // Not authenticated - show access denied
  if (!isAdminAuthenticated) {
    return (
      <>
        <div className="flex flex-col items-center justify-center min-h-[60vh] gap-6">
          <Card className="max-w-md w-full">
            <CardHeader className="text-center space-y-4">
              <div className="mx-auto flex h-20 w-20 items-center justify-center rounded-full bg-gradient-to-br from-orange-100 to-orange-50 dark:from-orange-950/30 dark:to-orange-900/20 border border-orange-200 dark:border-orange-800">
                <Lock className="h-10 w-10 text-orange-600 dark:text-orange-400" />
              </div>
              <div>
                <CardTitle className="text-xl">管理者権限が必要です</CardTitle>
                <CardDescription className="mt-2">
                  このエリアにアクセスするには、管理者トークンで認証してください
                </CardDescription>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              <Button
                className="w-full"
                onClick={() => setShowAuthModal(true)}
              >
                <Shield className="mr-2 h-4 w-4" />
                管理者トークンを入力
              </Button>
              <Button
                variant="outline"
                className="w-full"
                onClick={() => router.push("/")}
              >
                <ArrowLeft className="mr-2 h-4 w-4" />
                ダッシュボードに戻る
              </Button>
            </CardContent>
          </Card>

          <p className="text-xs text-muted-foreground text-center max-w-sm">
            管理者権限では、ユーザー、APIトークン、システム設定を管理できます。
            アクセスが必要な場合は管理者に確認してください。
          </p>
        </div>

        <AdminAuthModal
          open={showAuthModal}
          onOpenChange={setShowAuthModal}
        />
      </>
    );
  }

  // Authenticated - render children
  return <>{children}</>;
}
