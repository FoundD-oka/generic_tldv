"use client";

import { useEffect, useState } from "react";
import {
  Webhook,
  Loader2,
  Eye,
  EyeOff,
  RotateCw,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";
import { useWebhookStore, type WebhookDeliveryStatus } from "@/stores/webhook-store";
import { useAuthStore } from "@/stores/auth-store";
import { cn } from "@/lib/utils";

const WEBHOOK_EVENTS = [
  { key: "meeting.completed", label: "meeting.completed", defaultEnabled: true },
  { key: "meeting.started", label: "meeting.started", defaultEnabled: false },
  { key: "bot.failed", label: "bot.failed", defaultEnabled: false },
  { key: "meeting.status_change", label: "meeting.status_change", defaultEnabled: false },
];

function getDeliveryStatusLabel(status: WebhookDeliveryStatus): string {
  switch (status) {
    case "delivered":
      return "配信済み";
    case "retrying":
      return "再試行中";
    case "failed":
      return "失敗";
    default:
      return status;
  }
}

function StatusDot({ status }: { status: WebhookDeliveryStatus }) {
  return (
    <span
      className={cn(
        "inline-block w-2 h-2 rounded-full",
        status === "delivered" && "bg-emerald-400",
        status === "retrying" && "bg-amber-400",
        status === "failed" && "bg-red-400"
      )}
    />
  );
}

function StatusBadge({ code }: { code: number | null }) {
  if (code === null) return <span className="inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium bg-red-900/30 text-red-300">タイムアウト</span>;
  const isSuccess = code >= 200 && code < 300;
  return (
    <span
      className={cn(
        "inline-flex items-center px-2 py-0.5 rounded text-[11px] font-medium",
        isSuccess ? "bg-emerald-900/30 text-emerald-300" : "bg-red-900/30 text-red-300"
      )}
    >
      {code}
    </span>
  );
}

function formatResponseTime(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "30秒";
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}秒`;
  return `${ms}ミリ秒`;
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr);
  return d.toLocaleDateString("ja-JP", { month: "short", day: "numeric" }) +
    ", " +
    d.toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit", hour12: false });
}

export default function WebhooksPage() {
  const user = useAuthStore((state) => state.user);
  const {
    config: webhookConfig,
    deliveries,
    stats,
    isLoading,
    isLoadingConfig: isLoadingWebhookConfig,
    isSavingConfig,
    statusFilter,
    timeRange,
    setStatusFilter,
    setTimeRange,
    setUserId,
    fetchDeliveries,
    fetchConfig: fetchWebhookConfig,
    saveConfig: saveWebhookConfig,
    testWebhook,
    rotateSecret,
  } = useWebhookStore();

  const [webhookUrl, setWebhookUrl] = useState("");
  const [webhookSecret, setWebhookSecret] = useState("");
  const [webhookEvents, setWebhookEvents] = useState<Record<string, boolean>>({});
  const [showSecret, setShowSecret] = useState(false);
  const [isTesting, setIsTesting] = useState(false);
  const [isRotating, setIsRotating] = useState(false);

  useEffect(() => {
    if (user?.id) {
      setUserId(Number(user.id));
    }
  }, [user?.id, setUserId]);

  useEffect(() => {
    if (user?.id) {
      fetchDeliveries();
      fetchWebhookConfig();
    }
  }, [user?.id, fetchDeliveries, fetchWebhookConfig]);

  // Sync webhook config to local state
  useEffect(() => {
    if (webhookConfig) {
      setWebhookUrl(webhookConfig.endpoint_url || "");
      setWebhookSecret(webhookConfig.signing_secret_masked || "");
      setWebhookEvents(webhookConfig.events || {});
    }
  }, [webhookConfig]);

  const handleTestWebhook = async () => {
    if (!webhookUrl) return;
    setIsTesting(true);
    try {
      const result = await testWebhook(webhookUrl);
      if (result.success) {
        toast.success("Webhookテストに成功しました", {
          description: `ステータス ${result.status} / ${formatResponseTime(result.time_ms)}`,
        });
      } else {
        toast.error("Webhookテストに失敗しました", { description: result.error });
      }
    } catch (error) {
      toast.error("テストに失敗しました", { description: (error as Error).message });
    } finally {
      setIsTesting(false);
      fetchDeliveries();
    }
  };

  const handleRotateSecret = async () => {
    setIsRotating(true);
    try {
      await rotateSecret();
      toast.success("署名シークレットを更新しました", {
        description: "新しいシークレットはすぐに有効になります。",
      });
      setShowSecret(true);
      setTimeout(() => setShowSecret(false), 10000);
    } catch (error) {
      toast.error("シークレットの更新に失敗しました", { description: (error as Error).message });
    } finally {
      setIsRotating(false);
    }
  };

  const handleSaveWebhookConfig = async () => {
    try {
      await saveWebhookConfig({
        endpoint_url: webhookUrl,
        events: webhookEvents,
      });
      toast.success("Webhook設定を保存しました");
    } catch (error) {
      toast.error("設定の保存に失敗しました", { description: (error as Error).message });
    }
  };

  const toggleEvent = (key: string) => {
    setWebhookEvents((prev) => ({ ...prev, [key]: !prev[key] }));
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-foreground">
          Webhook設定
        </h1>
        <p className="text-sm text-muted-foreground">
          会議イベントのWebhook配信を設定し、配信履歴を確認できます
        </p>
      </div>

      {/* Webhook Configuration */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Webhook className="h-5 w-5" />
            配信設定
          </CardTitle>
          <CardDescription>
            会議イベントを受け取るエンドポイントと対象イベントを設定します
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Endpoint URL */}
          <div className="space-y-1.5">
            <Label className="text-sm text-muted-foreground">エンドポイントURL</Label>
            <div className="flex gap-2">
              <Input
                type="url"
                placeholder="https://your-server.com/webhook"
                value={webhookUrl}
                onChange={(e) => setWebhookUrl(e.target.value)}
                className="flex-1 font-mono text-sm"
              />
              <Button
                variant="secondary"
                onClick={handleTestWebhook}
                disabled={isTesting || !webhookUrl}
              >
                {isTesting ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  "テスト"
                )}
              </Button>
            </div>
          </div>

          {/* Signing Secret */}
          <div className="space-y-1.5">
            <Label className="text-sm text-muted-foreground">署名シークレット</Label>
            <div className="flex items-center gap-2">
              <Input
                type={showSecret ? "text" : "password"}
                value={webhookSecret}
                onChange={(e) => setWebhookSecret(e.target.value)}
                placeholder="whsec_... または任意のシークレットを入力"
                className="flex-1 font-mono text-sm"
              />
              <Button
                variant="secondary"
                size="sm"
                onClick={() => setShowSecret(!showSecret)}
              >
                {showSecret ? (
                  <EyeOff className="h-4 w-4" />
                ) : (
                  <Eye className="h-4 w-4" />
                )}
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={handleRotateSecret}
                disabled={isRotating}
              >
                {isRotating ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <RotateCw className="h-4 w-4" />
                )}
              </Button>
            </div>
            <p className="text-[11px] text-muted-foreground">
              Webhook署名の検証に使うシークレットです。
            </p>
          </div>

          {/* Event toggles */}
          <div className="space-y-1.5">
            <Label className="text-sm text-muted-foreground">イベント</Label>
            <div className="flex flex-wrap gap-2">
              {WEBHOOK_EVENTS.map((event) => {
                const enabled = webhookEvents[event.key] ?? event.defaultEnabled;
                return (
                  <button
                    key={event.key}
                    type="button"
                    onClick={() => toggleEvent(event.key)}
                    className={cn(
                      "inline-flex items-center px-2.5 py-1 rounded-lg text-xs font-medium border cursor-pointer transition-colors",
                      enabled
                        ? "bg-emerald-100 text-emerald-700 border-emerald-300 dark:bg-emerald-900/30 dark:text-emerald-300 dark:border-emerald-800/30"
                        : "bg-muted text-muted-foreground border-border hover:border-muted-foreground/30"
                    )}
                  >
                    {event.label}
                    {enabled && " \u2713"}
                  </button>
                );
              })}
            </div>
          </div>

          {/* Save */}
          <div className="flex justify-end pt-2">
            <Button
              onClick={handleSaveWebhookConfig}
              disabled={isSavingConfig}
            >
              {isSavingConfig ? (
                <>
                  <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                  保存中...
                </>
              ) : (
                "保存"
              )}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Delivery History */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold tracking-[-0.02em] text-foreground">
            配信履歴
          </h2>
        </div>
        <div className="flex items-center gap-3">
          <Select
            value={statusFilter}
            onValueChange={(v) => setStatusFilter(v as WebhookDeliveryStatus | "all")}
          >
            <SelectTrigger className="w-[150px]">
              <SelectValue placeholder="すべての状態" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">すべての状態</SelectItem>
              <SelectItem value="delivered">配信済み</SelectItem>
              <SelectItem value="retrying">再試行中</SelectItem>
              <SelectItem value="failed">失敗</SelectItem>
            </SelectContent>
          </Select>
          <Select
            value={timeRange}
            onValueChange={(v) => setTimeRange(v as "24h" | "7d" | "30d")}
          >
            <SelectTrigger className="w-[150px]">
              <SelectValue placeholder="過去7日" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="24h">過去24時間</SelectItem>
              <SelectItem value="7d">過去7日</SelectItem>
              <SelectItem value="30d">過去30日</SelectItem>
            </SelectContent>
          </Select>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-xs text-muted-foreground mb-0.5">合計</p>
            <p className="text-xl font-semibold">{stats.total}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-xs text-muted-foreground mb-0.5">配信済み</p>
            <p className="text-xl font-semibold text-emerald-400">{stats.delivered}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-xs text-muted-foreground mb-0.5">再試行中</p>
            <p className="text-xl font-semibold text-amber-400">{stats.retrying}</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="pt-4 pb-4">
            <p className="text-xs text-muted-foreground mb-0.5">失敗</p>
            <p className="text-xl font-semibold text-red-400">{stats.failed}</p>
          </CardContent>
        </Card>
      </div>

      {/* Deliveries table */}
      <Card className="overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead>
              <tr className="border-b text-xs text-muted-foreground uppercase tracking-wider">
                <th className="text-left px-5 py-3 font-medium">イベント</th>
                <th className="text-left px-5 py-3 font-medium">会議</th>
                <th className="text-left px-5 py-3 font-medium">状態</th>
                <th className="text-left px-5 py-3 font-medium">試行回数</th>
                <th className="text-left px-5 py-3 font-medium">レスポンス</th>
                <th className="text-left px-5 py-3 font-medium">日時</th>
              </tr>
            </thead>
            <tbody className="text-sm">
              {isLoading ? (
                <tr>
                  <td colSpan={6} className="px-5 py-12 text-center">
                    <Loader2 className="h-5 w-5 animate-spin mx-auto text-muted-foreground" />
                  </td>
                </tr>
              ) : deliveries.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-5 py-12 text-center">
                    <div className="flex flex-col items-center gap-2">
                      <Webhook className="h-8 w-8 text-muted-foreground/50" />
                      <p className="text-sm text-muted-foreground">
                        Webhook配信履歴はまだありません
                      </p>
                    </div>
                  </td>
                </tr>
              ) : (
                deliveries.map((delivery) => (
                  <tr
                    key={delivery.id}
                    className="border-b border-border/50 hover:bg-muted/30 cursor-pointer transition-colors"
                  >
                    <td className="px-5 py-3">
                      <span className="font-mono text-xs text-muted-foreground">
                        {delivery.event}
                      </span>
                    </td>
                    <td className="px-5 py-3 font-medium">{delivery.meeting_name}</td>
                    <td className="px-5 py-3">
                      <span className="inline-flex items-center gap-1.5">
                        <StatusDot status={delivery.status} />
                        <span
                          className={cn(
                            "text-xs",
                            delivery.status === "delivered" && "text-emerald-400",
                            delivery.status === "retrying" && "text-amber-400",
                            delivery.status === "failed" && "text-red-400"
                          )}
                        >
                          {getDeliveryStatusLabel(delivery.status)}
                        </span>
                      </span>
                    </td>
                    <td className="px-5 py-3 text-muted-foreground">
                      {delivery.attempts}/{delivery.max_attempts}
                    </td>
                    <td className="px-5 py-3">
                      <StatusBadge code={delivery.response_status} />{" "}
                      <span className="text-xs text-muted-foreground">
                        {formatResponseTime(delivery.response_time_ms)}
                      </span>
                    </td>
                    <td className="px-5 py-3 text-muted-foreground text-xs">
                      {formatDate(delivery.last_attempt_at)}
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}
