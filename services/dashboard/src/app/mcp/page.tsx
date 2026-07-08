"use client";

import { useState, useEffect } from "react";
import { Copy, Check, Code, Settings } from "lucide-react";
import Image from "next/image";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";
import { withBasePath } from "@/lib/base-path";

interface RuntimeConfig {
  wsUrl: string;
  apiUrl: string;
  authToken: string | null;
}

export default function MCPPage() {
  const [config, setConfig] = useState<RuntimeConfig | null>(null);
  const [copied, setCopied] = useState(false);
  const [loading, setLoading] = useState(true);
  const [mcpIconError, setMcpIconError] = useState(false);

  useEffect(() => {
    async function fetchConfig() {
      try {
        const response = await fetch(withBasePath("/api/config"));
        const data = await response.json();
        setConfig(data);
      } catch (error) {
        console.error("Failed to fetch config:", error);
        toast.error("設定の読み込みに失敗しました");
      } finally {
        setLoading(false);
      }
    }
    fetchConfig();
  }, []);

  const getMCPUrl = () => {
    const base = config?.apiUrl;
    if (!base) {
      return "http://localhost:8056/mcp";
    }
    return `${base.replace(/\/$/, "")}/mcp`;
  };

  const maskKey = (key: string): string => {
    if (key.length <= 12) return key;
    return `${key.slice(0, 8)}${"*".repeat(8)}${key.slice(-4)}`;
  };

  const buildMCPConfig = (masked: boolean): string => {
    const mcpUrl = getMCPUrl();
    const rawKey = config?.authToken || "<api-key>";
    const apiKey = masked && config?.authToken ? maskKey(rawKey) : rawKey;

    return JSON.stringify({
      mcpServers: {
        kabosu: {
          command: "npx",
          args: [
            "-y",
            "mcp-remote",
            mcpUrl,
            "--header",
            "Authorization:${VEXA_API_KEY}",
          ],
          env: {
            VEXA_API_KEY: apiKey,
          },
        },
      },
    }, null, 2);
  };

  const displayConfig = () => buildMCPConfig(true);
  const copyableConfig = () => buildMCPConfig(false);

  const copyToClipboard = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      toast.success("クリップボードにコピーしました");
      setTimeout(() => setCopied(false), 2000);
    } catch {
      toast.error("コピーに失敗しました");
    }
  };

  const handleCursorInstall = () => {
    if (!config?.authToken) {
      toast.error("APIトークンがありません。先にプロフィールでAPIキーを作成してください。");
      return;
    }

    const mcpUrl = getMCPUrl();
    const apiKey = config.authToken;

    const mcpServerConfig = {
      command: "npx",
      args: ["-y", "mcp-remote", mcpUrl, "--header", "Authorization:${VEXA_API_KEY}"],
      env: { VEXA_API_KEY: apiKey },
    };

    const fullMCPConfig = { mcpServers: { kabosu: mcpServerConfig } };
    const configJson = JSON.stringify(fullMCPConfig, null, 2);
    copyToClipboard(configJson);

    try {
      const configBase64 = btoa(JSON.stringify(mcpServerConfig));
      const configEncoded = encodeURIComponent(configBase64);
      const deepLink = `cursor://anysphere.cursor-deeplink/mcp/install?name=kabosu&config=${configEncoded}`;

      const link = document.createElement("a");
      link.href = deepLink;
      link.style.display = "none";
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);

      toast.success("CursorでMCPサーバーのインストールを開いています...", {
        description: "Cursorが自動で開かない場合は、設定をクリップボードにコピー済みです。",
        duration: 8000,
      });
    } catch {
      toast.info("設定をクリップボードにコピーしました", {
        description: "~/.cursor/mcp.json に貼り付け、必要に応じて既存の mcpServers に統合してください。",
        duration: 8000,
      });
    }
  };

  const handleVSCodeInstall = () => {
    if (!config?.authToken) {
      toast.error("APIトークンがありません。先にプロフィールでAPIキーを作成してください。");
      return;
    }

    const configJson = copyableConfig();
    copyToClipboard(configJson);

    const blob = new Blob([configJson], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "mcp.json";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);

    toast.success("設定をダウンロードし、クリップボードにもコピーしました", {
      description: "ダウンロードした mcp.json を ~/.vscode/mcp.json に保存するか、既存ファイルへ統合してください。",
      duration: 10000,
    });
  };

  const MCPIcon = () => {
    if (mcpIconError) {
      return <Code className="h-5 w-5" />;
    }
    return (
      <div className="h-5 w-5 relative flex items-center justify-center">
        <img
          src={withBasePath("/icons/icons8-mcp-96.png")}
          alt="MCP"
          width={20}
          height={20}
          className="object-contain dark:invert"
          onError={() => setMcpIconError(true)}
        />
      </div>
    );
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-foreground">
          MCP設定
        </h1>
        <p className="text-sm text-muted-foreground">
          AIコーディングアシスタントをModel Context Protocol経由でカボスに接続します
        </p>
      </div>

      {/* Quick Install */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Card className="cursor-pointer hover:bg-muted/30 transition-colors" onClick={handleCursorInstall}>
          <CardContent className="pt-6 pb-6 flex items-center gap-4">
            <div className="h-10 w-10 rounded-lg bg-muted flex items-center justify-center">
              <Image src={withBasePath("/icons/cursor.svg")} alt="Cursor" width={24} height={24} className="dark:invert" unoptimized />
            </div>
            <div>
              <p className="font-medium">Cursorに接続</p>
              <p className="text-xs text-muted-foreground">ディープリンクでワンクリック設定</p>
            </div>
          </CardContent>
        </Card>
        <Card className="cursor-pointer hover:bg-muted/30 transition-colors" onClick={handleVSCodeInstall}>
          <CardContent className="pt-6 pb-6 flex items-center gap-4">
            <div className="h-10 w-10 rounded-lg bg-muted flex items-center justify-center">
              <Image src={withBasePath("/icons/vscode.svg")} alt="VS Code" width={24} height={24} unoptimized />
            </div>
            <div>
              <p className="font-medium">VS Codeに接続</p>
              <p className="text-xs text-muted-foreground">mcp.json設定ファイルをダウンロード</p>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Manual Configuration */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Settings className="h-5 w-5" />
            設定
          </CardTitle>
          <CardDescription>
            このJSONをコピーして、エディタの mcp.json に追加してください
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="relative">
            <Textarea
              value={loading ? "読み込み中..." : displayConfig()}
              readOnly
              className="font-mono text-sm min-h-[220px]"
            />
            <Button
              variant="outline"
              size="sm"
              className="absolute top-2 right-2"
              onClick={() => copyToClipboard(copyableConfig())}
              disabled={loading}
            >
              {copied ? (
                <Check className="h-4 w-4 text-green-500" />
              ) : (
                <Copy className="h-4 w-4" />
              )}
            </Button>
          </div>
          <div className="text-sm text-muted-foreground space-y-2 p-4 bg-muted rounded-lg">
            <p>
              <strong>Cursor:</strong>{" "}
              <code className="bg-background px-1.5 py-0.5 rounded text-xs">~/.cursor/mcp.json</code>
            </p>
            <p>
              <strong>VS Code:</strong>{" "}
              <code className="bg-background px-1.5 py-0.5 rounded text-xs">~/.vscode/mcp.json</code>
            </p>
            <p>
              <strong>Claude Code:</strong>{" "}
              <code className="bg-background px-1.5 py-0.5 rounded text-xs">~/.claude/mcp.json</code>
            </p>
            <p className="text-xs pt-2">
              すでに mcp.json がある場合は、kabosu の項目を既存の{" "}
              <code className="bg-background px-1.5 py-0.5 rounded text-xs">mcpServers</code>
              に統合してください。
            </p>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
