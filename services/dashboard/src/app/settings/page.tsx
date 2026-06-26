"use client";

import { useState, useEffect } from "react";
import { Settings, CheckCircle2, XCircle, Loader2, ExternalLink, Sparkles, AlertCircle } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { toast } from "sonner";
import { vexaAPI } from "@/lib/api";
import { AdminGuard } from "@/components/admin/admin-guard";
import { withBasePath } from "@/lib/base-path";

interface AIConfig {
  enabled: boolean;
  provider: string | null;
  model: string | null;
  hasApiKey?: boolean;
  hasBaseUrl?: boolean;
}

interface RuntimeConfig {
  wsUrl: string;
  apiUrl: string;
}

function SettingsContent() {
  const [isTesting, setIsTesting] = useState(false);
  const [connectionStatus, setConnectionStatus] = useState<"unknown" | "connected" | "error">("unknown");
  const [connectionError, setConnectionError] = useState<string | null>(null);
  const [aiConfig, setAIConfig] = useState<AIConfig | null>(null);
  const [isLoadingAIConfig, setIsLoadingAIConfig] = useState(true);
  const [runtimeConfig, setRuntimeConfig] = useState<RuntimeConfig | null>(null);

  // Fetch configurations on mount
  useEffect(() => {
    async function fetchConfigs() {
      // Fetch runtime config (WebSocket URL)
      try {
        const configResponse = await fetch(withBasePath("/api/config"));
        const config = await configResponse.json();
        setRuntimeConfig(config);
      } catch (error) {
        console.error("Failed to fetch runtime config:", error);
      }

      // Fetch AI config
      try {
        const response = await fetch(withBasePath("/api/ai/config"));
        const config = await response.json();
        setAIConfig(config);
      } catch (error) {
        console.error("Failed to fetch AI config:", error);
        setAIConfig({ enabled: false, provider: null, model: null });
      } finally {
        setIsLoadingAIConfig(false);
      }
    }
    fetchConfigs();
  }, []);

  const handleTestConnection = async () => {
    setIsTesting(true);
    setConnectionStatus("unknown");
    setConnectionError(null);

    try {
      const result = await vexaAPI.testConnection();
      if (result.success) {
        setConnectionStatus("connected");
        toast.success("接続に成功しました", {
          description: "Vexa APIに接続できました",
        });
      } else {
        setConnectionStatus("error");
        setConnectionError(result.error || "不明なエラー");
        toast.error("接続に失敗しました", {
          description: result.error,
        });
      }
    } catch (error) {
      setConnectionStatus("error");
      setConnectionError((error as Error).message);
      toast.error("接続に失敗しました", {
        description: (error as Error).message,
      });
    } finally {
      setIsTesting(false);
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-3xl font-bold tracking-tight">設定</h1>
        <p className="text-muted-foreground">
          カボス ダッシュボードの接続設定を確認します
        </p>
      </div>

      <div className="max-w-2xl space-y-6">
        {/* API Configuration */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Settings className="h-5 w-5" />
              Vexa API設定
            </CardTitle>
            <CardDescription>
              Vexaインスタンスへの接続情報です。これらの値は環境変数で管理されています。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {/* API URL */}
            <div className="space-y-2">
              <Label htmlFor="apiUrl">API URL</Label>
              <Input
                id="apiUrl"
                value={runtimeConfig?.apiUrl || "読み込み中..."}
                disabled
                className="font-mono bg-muted"
              />
              <p className="text-xs text-muted-foreground">
                <code className="bg-muted px-1 rounded">VEXA_API_URL</code> 環境変数で設定します
              </p>
            </div>

            {/* WebSocket URL */}
            <div className="space-y-2">
              <Label htmlFor="wsUrl">WebSocket URL</Label>
              <Input
                id="wsUrl"
                value={runtimeConfig?.wsUrl || "読み込み中..."}
                disabled
                className="font-mono bg-muted"
              />
              <p className="text-xs text-muted-foreground">
                <code className="bg-muted px-1 rounded">VEXA_API_URL</code> から自動生成されます
              </p>
            </div>

            {/* Admin API Key Status */}
            <div className="space-y-2">
              <Label>管理者APIキー</Label>
              <div className="flex items-center gap-2">
                <Input
                  value="••••••••••••••••••••••••••••••••"
                  disabled
                  className="font-mono bg-muted"
                />
                <Badge variant="secondary">設定済み</Badge>
              </div>
              <p className="text-xs text-muted-foreground">
                <code className="bg-muted px-1 rounded">VEXA_ADMIN_API_KEY</code> 環境変数で設定します
              </p>
            </div>

            <Separator />

            {/* Test Connection */}
            <div className="flex items-center justify-between">
              <div className="space-y-1">
                <p className="font-medium">接続状態</p>
                <div className="flex items-center gap-2">
                  {connectionStatus === "connected" && (
                    <>
                      <CheckCircle2 className="h-4 w-4 text-green-500" />
                      <span className="text-sm text-green-600">接続済み</span>
                    </>
                  )}
                  {connectionStatus === "error" && (
                    <>
                      <XCircle className="h-4 w-4 text-red-500" />
                      <span className="text-sm text-red-600">
                        {connectionError || "接続に失敗しました"}
                      </span>
                    </>
                  )}
                  {connectionStatus === "unknown" && (
                    <span className="text-sm text-muted-foreground">未確認</span>
                  )}
                </div>
              </div>
              <Button onClick={handleTestConnection} disabled={isTesting}>
                {isTesting ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    テスト中...
                  </>
                ) : (
                  "接続テスト"
                )}
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* AI Configuration */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Sparkles className="h-5 w-5" />
              AIアシスタント設定
            </CardTitle>
            <CardDescription>
              会議文字起こし分析用のAI設定です。環境変数で管理されています。
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {isLoadingAIConfig ? (
              <div className="flex items-center gap-2 text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                <span>AI設定を確認中...</span>
              </div>
            ) : aiConfig?.enabled ? (
              <>
                {/* Status */}
                <div className="flex items-center gap-2">
                  <CheckCircle2 className="h-5 w-5 text-green-500" />
                  <span className="font-medium text-green-600">AIアシスタント有効</span>
                </div>

                {/* Provider */}
                <div className="space-y-2">
                  <Label>プロバイダー</Label>
                  <Input
                    value={aiConfig.provider || "不明"}
                    disabled
                    className="font-mono bg-muted capitalize"
                  />
                </div>

                {/* Model */}
                <div className="space-y-2">
                  <Label>モデル</Label>
                  <Input
                    value={aiConfig.model || "不明"}
                    disabled
                    className="font-mono bg-muted"
                  />
                </div>

                {/* API Key Status */}
                <div className="space-y-2">
                  <Label>APIキー</Label>
                  <div className="flex items-center gap-2">
                    <Input
                      value="••••••••••••••••••••••••••••••••"
                      disabled
                      className="font-mono bg-muted"
                    />
                    <Badge variant={aiConfig.hasApiKey ? "secondary" : "destructive"}>
                      {aiConfig.hasApiKey ? "設定済み" : "未設定"}
                    </Badge>
                  </div>
                </div>

                {/* Base URL (if set) */}
                {aiConfig.hasBaseUrl && (
                  <div className="space-y-2">
                    <Label>カスタムBase URL</Label>
                    <div className="flex items-center gap-2">
                      <Input
                        value="カスタムエンドポイント設定済み"
                        disabled
                        className="bg-muted"
                      />
                      <Badge variant="secondary">設定済み</Badge>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <div className="flex flex-col items-center justify-center py-8 text-center">
                <div className="h-12 w-12 rounded-full bg-muted flex items-center justify-center mb-4">
                  <AlertCircle className="h-6 w-6 text-muted-foreground" />
                </div>
                <h3 className="font-medium mb-1">AIが未設定です</h3>
                <p className="text-sm text-muted-foreground max-w-sm">
                  AI機能を有効にするには <code className="bg-muted px-1 rounded">AI_MODEL</code> と{" "}
                  <code className="bg-muted px-1 rounded">AI_API_KEY</code> 環境変数を設定してください。
                </p>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Environment Variables */}
        <Card>
          <CardHeader>
            <CardTitle>環境変数</CardTitle>
            <CardDescription>
              ダッシュボードを設定するには、以下の変数を含む <code className="bg-muted px-1 rounded">.env.local</code> を作成してください
            </CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="bg-muted p-4 rounded-lg text-sm overflow-x-auto">
{`# Vexa API Configuration (required)
	VEXA_API_URL=<your-api-gateway-url>
VEXA_ADMIN_API_KEY=your_admin_api_key_here

# AI Assistant Configuration (optional)
# Format: provider/model
AI_MODEL=openai/gpt-4o
AI_API_KEY=your_ai_api_key_here`}
            </pre>
          </CardContent>
        </Card>

        {/* About */}
        <Card>
          <CardHeader>
            <CardTitle>このダッシュボードについて</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-muted-foreground">バージョン</span>
              <span className="font-medium">1.0.0</span>
            </div>
            <Separator />
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground">
                カボス ダッシュボードは、セルフホスト可能な会議文字起こしAPIであるVexa向けのWeb管理画面です。
              </p>
              <div className="flex gap-4">
                <a
                  href="https://github.com/Vexa-ai/vexa"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-primary hover:underline inline-flex items-center gap-1"
                >
                  Vexa GitHub
                  <ExternalLink className="h-3 w-3" />
                </a>
                <a
                  href="https://vexa.ai"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-primary hover:underline inline-flex items-center gap-1"
                >
                  Vexa Website
                  <ExternalLink className="h-3 w-3" />
                </a>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

export default function SettingsPage() {
  return (
    <AdminGuard>
      <SettingsContent />
    </AdminGuard>
  );
}
