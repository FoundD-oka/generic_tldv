import { VexaAPIError } from "@/lib/api";

export interface UserFriendlyError {
  title: string;
  description: string;
}

/**
 * Converts API errors into user-friendly messages
 */
export function getUserFriendlyError(error: Error): UserFriendlyError {
  const message = error.message.toLowerCase();

  // Concurrent bot limit reached
  if (message.includes("concurrent") && message.includes("limit")) {
    return {
      title: "ボット数が上限に達しました",
      description: "同時に起動できるボット数の上限に達しています。既存のボットを停止してからもう一度お試しください。",
    };
  }

  // Rate limiting
  if (message.includes("rate limit") || message.includes("too many requests")) {
    return {
      title: "リクエストが多すぎます",
      description: "しばらくしてからもう一度お試しください。",
    };
  }

  // Authentication errors
  if (error instanceof VexaAPIError && error.status === 401) {
    return {
      title: "認証に失敗しました",
      description: "セッションの有効期限が切れた可能性があります。ログインし直してください。",
    };
  }

  // Forbidden
  if (error instanceof VexaAPIError && error.status === 403) {
    return {
      title: "アクセス権がありません",
      description: error.message || "この操作を実行する権限がありません。",
    };
  }

  // Server errors
  if (error instanceof VexaAPIError && error.status >= 500) {
    return {
      title: "サーバーエラーが発生しました",
      description: "時間をおいてもう一度お試しください。",
    };
  }

  // Network errors
  if (message.includes("network") || message.includes("fetch")) {
    return {
      title: "接続エラーが発生しました",
      description: "サーバーに接続できません。ネットワーク接続を確認してください。",
    };
  }

  // Default error
  return {
    title: "問題が発生しました",
    description: error.message || "予期しないエラーが発生しました。",
  };
}
