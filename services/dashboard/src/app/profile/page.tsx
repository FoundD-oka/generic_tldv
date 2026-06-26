"use client";

import { useState, useEffect } from "react";
import {
  User,
  Key,
  Copy,
  Loader2,
  Plus,
  Check,
  GitBranch,
  RefreshCw,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import { useAuthStore } from "@/stores/auth-store";
import { cn } from "@/lib/utils";
import { withBasePath } from "@/lib/base-path";

// ==========================================
// Types
// ==========================================

interface APIKeyDisplay {
  id: string;
  name: string;
  scopes: KeyScope[];
  token: string;
  masked_token: string;
  created_at: string;
  last_used_at: string | null;
  expires_at: string | null;
}

type KeyScope = "bot" | "tx" | "browser";

const SCOPE_CONFIG: Record<KeyScope, { label: string; prefix: string; color: string; bgColor: string }> = {
  bot: { label: "ボット", prefix: "vxa_bot_", color: "text-purple-300", bgColor: "bg-purple-900/40" },
  tx: { label: "文字起こし", prefix: "vxa_tx_", color: "text-cyan-300", bgColor: "bg-cyan-900/40" },
  browser: { label: "ブラウザ", prefix: "vxa_browser_", color: "text-emerald-300", bgColor: "bg-emerald-900/40" },
};

// ==========================================
// Helpers
// ==========================================

function inferScope(token: string): KeyScope {
  if (token.startsWith("vxa_tx_")) return "tx";
  if (token.startsWith("vxa_browser_")) return "browser";
  return "bot";
}

function maskToken(token: string): string {
  if (token.length < 16) return token;
  // Find prefix end (after vxa_xxx_)
  const prefixMatch = token.match(/^(vxa_\w+_)/);
  if (prefixMatch) {
    const prefix = prefixMatch[1];
    const rest = token.slice(prefix.length);
    if (rest.length >= 8) {
      return `${prefix}${rest.slice(0, 4)}••••${rest.slice(-4)}`;
    }
    return `${prefix}${rest}`;
  }
  return `${token.slice(0, 8)}••••${token.slice(-4)}`;
}

function relativeTime(dateStr: string): string {
  const diff = Date.now() - new Date(dateStr).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return "たった今";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}分前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}時間前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}日前`;
  return new Date(dateStr).toLocaleDateString("ja-JP", { month: "long", day: "numeric" });
}

function formatExpiry(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString("ja-JP", { month: "long", day: "numeric" });
}

function scopesFromApi(scopes: string[]): KeyScope[] {
  const valid: KeyScope[] = ["bot", "tx", "browser"];
  const result = scopes.filter((s): s is KeyScope => valid.includes(s as KeyScope));
  return result.length > 0 ? result : ["bot"];
}

// ==========================================
// Component
// ==========================================

export default function ProfilePage() {
  const user = useAuthStore((state) => state.user);

  // API Keys state
  const [apiKeys, setApiKeys] = useState<APIKeyDisplay[]>([]);
  const [isLoadingKeys, setIsLoadingKeys] = useState(true);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [newKeyName, setNewKeyName] = useState("");
  const [newKeyScopes, setNewKeyScopes] = useState<Set<KeyScope>>(new Set(["bot", "tx", "browser"]));
  const [newKeyExpiry, setNewKeyExpiry] = useState<string>("");
  const [isCreatingKey, setIsCreatingKey] = useState(false);
  const [createdKeyToken, setCreatedKeyToken] = useState<string | null>(null);
  const [copiedKeyId, setCopiedKeyId] = useState<string | null>(null);


  // Fetch API keys
  useEffect(() => {
    async function fetchKeys() {
      if (!user?.id) return;
      try {
        const response = await fetch(withBasePath(`/api/profile/keys?userId=${user.id}`));
        if (!response.ok) {
          // Graceful fallback — endpoint may not exist yet
          setApiKeys([]);
          return;
        }
        const data = await response.json();
        setApiKeys(
          (data.keys || []).map((k: { id: string; token: string; scopes?: string[]; name?: string; created_at: string; last_used_at?: string; expires_at?: string }) => ({
            id: k.id,
            name: k.name || "APIキー",
            scopes: k.scopes && k.scopes.length > 0 ? scopesFromApi(k.scopes) : [inferScope(k.token)],
            token: k.token,
            masked_token: maskToken(k.token),
            created_at: k.created_at,
            last_used_at: k.last_used_at || null,
            expires_at: k.expires_at || null,
          }))
        );
      } catch {
        setApiKeys([]);
      } finally {
        setIsLoadingKeys(false);
      }
    }
    fetchKeys();
  }, [user?.id]);


  const handleCreateKey = async () => {
    setIsCreatingKey(true);
    try {
      const scopes = Array.from(newKeyScopes).join(",");
      const response = await fetch(withBasePath("/api/profile/keys"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: newKeyName,
          scopes,
          userId: user?.id,
          ...(newKeyExpiry ? { expires_in: parseInt(newKeyExpiry) * 86400 } : {}),
        }),
      });
      if (!response.ok) throw new Error("APIキーの作成に失敗しました");
      const data = await response.json();
      setCreatedKeyToken(data.token);
      // Add to list
      setApiKeys((prev) => [
        ...prev,
        {
          id: data.id || String(Date.now()),
          name: newKeyName || "APIキー",
          scopes: data.scopes ? scopesFromApi(data.scopes) : Array.from(newKeyScopes),
          token: data.token,
          masked_token: maskToken(data.token),
          created_at: new Date().toISOString(),
          last_used_at: null,
          expires_at: null,
        },
      ]);
      toast.success("APIキーを作成しました");
    } catch (error) {
      toast.error("APIキーの作成に失敗しました", { description: (error as Error).message });
    } finally {
      setIsCreatingKey(false);
    }
  };

  const handleRevokeKey = async (keyId: string) => {
    try {
      const response = await fetch(withBasePath(`/api/profile/keys/${keyId}`), { method: "DELETE" });
      if (!response.ok) throw new Error("APIキーの取り消しに失敗しました");
      setApiKeys((prev) => prev.filter((k) => k.id !== keyId));
      toast.success("APIキーを取り消しました");
    } catch (error) {
      toast.error("APIキーの取り消しに失敗しました", { description: (error as Error).message });
    }
  };

  const handleCopyKey = async (keyId: string, token: string) => {
    await navigator.clipboard.writeText(token);
    setCopiedKeyId(keyId);
    setTimeout(() => setCopiedKeyId(null), 2000);
    toast.success("クリップボードにコピーしました");
  };


  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-foreground">プロフィール</h1>
        <p className="text-sm text-muted-foreground">
          アカウント情報とAPIキーを管理します
        </p>
      </div>

      <div className="max-w-2xl space-y-6">
        {/* Account info */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <User className="h-5 w-5" />
              アカウント
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">メールアドレス</span>
              <span>{user?.email || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">名前</span>
              <span>{user?.name || "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">同時稼働ボット上限</span>
              <span>{user?.max_concurrent_bots ?? "—"} 件</span>
            </div>
          </CardContent>
        </Card>

        {/* API Keys */}
        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="flex items-center gap-2">
                <Key className="h-5 w-5" />
                APIキー
              </CardTitle>
              <Button
                size="sm"
                onClick={() => {
                  setNewKeyName("");
                  setNewKeyScopes(new Set(["bot", "tx", "browser"]));
                  setNewKeyExpiry("");
                  setCreatedKeyToken(null);
                  setShowCreateDialog(true);
                }}
              >
                <Plus className="h-4 w-4 mr-1" />
                キーを作成
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {isLoadingKeys ? (
              <div className="flex items-center justify-center py-8">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
              </div>
            ) : apiKeys.length === 0 ? (
              <p className="text-sm text-muted-foreground py-4 text-center">
                APIキーはまだありません。まず1つ作成してください。
              </p>
            ) : (
              <div className="space-y-2">
                {apiKeys.map((key) => (
                  <div
                    key={key.id}
                    className="rounded-lg bg-muted/50 px-4 py-3 flex items-center justify-between"
                  >
                    <div>
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium">{key.name}</span>
                        {key.scopes.map((s) => (
                          <span
                            key={s}
                            className={cn(
                              "inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold",
                              SCOPE_CONFIG[s].bgColor,
                              SCOPE_CONFIG[s].color
                            )}
                          >
                            {SCOPE_CONFIG[s].label}
                          </span>
                        ))}
                      </div>
                      <p className="text-[11px] font-mono text-muted-foreground mt-0.5">
                        {key.masked_token}
                      </p>
                    </div>
                    <div className="flex items-center gap-3 text-xs">
                      <span className="text-muted-foreground" title="最終使用">
                        {key.last_used_at ? relativeTime(key.last_used_at) : "未使用"}
                      </span>
                      <span className="text-muted-foreground" title="有効期限">
                        {key.expires_at ? `期限 ${formatExpiry(key.expires_at)}` : "期限なし"}
                      </span>
                      <button
                        onClick={() => handleCopyKey(key.id, key.token)}
                        className="text-muted-foreground hover:text-foreground transition-colors"
                      >
                        {copiedKeyId === key.id ? (
                          <Check className="h-3.5 w-3.5 text-emerald-400" />
                        ) : (
                          <Copy className="h-3.5 w-3.5" />
                        )}
                      </button>
                      <button
                        onClick={() => handleRevokeKey(key.id)}
                        className="text-red-400 hover:text-red-300 transition-colors"
                      >
                        取り消し
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

      </div>

      {/* Create Key Dialog */}
      <Dialog open={showCreateDialog} onOpenChange={setShowCreateDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>APIキーを作成</DialogTitle>
            <DialogDescription>
              新しいAPIキーの用途と名前を設定します。
            </DialogDescription>
          </DialogHeader>

          {createdKeyToken ? (
            <div className="space-y-4">
              <div className="rounded-lg bg-emerald-950/30 border border-emerald-800/30 p-4">
                <p className="text-sm font-medium text-emerald-300 mb-2">
                  APIキーを作成しました
                </p>
                <p className="text-xs text-muted-foreground mb-3">
                  このキーは今だけ表示されます。必ず控えてください。
                </p>
                <div className="flex items-center gap-2">
                  <code className="flex-1 bg-muted rounded px-3 py-2 text-xs font-mono break-all">
                    {createdKeyToken}
                  </code>
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => {
                      navigator.clipboard.writeText(createdKeyToken);
                      toast.success("クリップボードにコピーしました");
                    }}
                  >
                    <Copy className="h-4 w-4" />
                  </Button>
                </div>
              </div>
              <DialogFooter>
                <Button onClick={() => setShowCreateDialog(false)}>完了</Button>
              </DialogFooter>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="space-y-2">
                <Label>キー名</Label>
                <Input
                  placeholder="例: 本番用ボットキー"
                  value={newKeyName}
                  onChange={(e) => setNewKeyName(e.target.value)}
                />
              </div>

              <div className="space-y-2">
                <Label>権限</Label>
                <div className="space-y-2">
                  {(["bot", "tx", "browser"] as const).map((scope) => {
                    const config = {
                      bot: { name: "ボット", desc: "会議ボット、Webhook、音声エージェント" },
                      tx: { name: "文字起こし", desc: "文字起こしと会議データの読み取り" },
                      browser: { name: "ブラウザ", desc: "ブラウザセッション、VNC、CDP、ワークスペース" },
                    }[scope];
                    const checked = newKeyScopes.has(scope);
                    return (
                      <button
                        key={scope}
                        type="button"
                        onClick={() => {
                          setNewKeyScopes((prev) => {
                            const next = new Set(prev);
                            if (next.has(scope)) {
                              next.delete(scope);
                            } else {
                              next.add(scope);
                            }
                            return next;
                          });
                        }}
                        className={cn(
                          "w-full p-3 rounded-lg border-2 text-left transition-all flex items-center gap-3",
                          checked
                            ? "border-foreground/20 bg-muted/50"
                            : "border-border hover:border-muted-foreground/30"
                        )}
                      >
                        <div className={cn(
                          "h-4 w-4 rounded border-2 flex items-center justify-center flex-shrink-0",
                          checked ? "border-foreground bg-foreground" : "border-muted-foreground/40"
                        )}>
                          {checked && <Check className="h-3 w-3 text-background" />}
                        </div>
                        <div className="flex-1">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-medium">{config.name}</span>
                            <span
                              className={cn(
                                "inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold",
                                SCOPE_CONFIG[scope].bgColor,
                                SCOPE_CONFIG[scope].color
                              )}
                            >
                              {SCOPE_CONFIG[scope].label}
                            </span>
                          </div>
                          <p className="text-[11px] text-muted-foreground">
                            {config.desc}
                          </p>
                        </div>
                      </button>
                    );
                  })}
                </div>
              </div>

              <div className="space-y-2">
                <Label>有効期限</Label>
                <div className="grid grid-cols-4 gap-2">
                  {[
                    { label: "なし", value: "" },
                    { label: "30日", value: "30" },
                    { label: "90日", value: "90" },
                    { label: "1年", value: "365" },
                  ].map((opt) => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => setNewKeyExpiry(opt.value)}
                      className={cn(
                        "px-3 py-1.5 rounded-md text-xs font-medium border transition-all",
                        newKeyExpiry === opt.value
                          ? "border-foreground/30 bg-muted"
                          : "border-border hover:border-muted-foreground/30"
                      )}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </div>

              <DialogFooter>
                <Button
                  variant="outline"
                  onClick={() => setShowCreateDialog(false)}
                >
                  キャンセル
                </Button>
                <Button
                  onClick={handleCreateKey}
                  disabled={isCreatingKey || !newKeyName.trim() || newKeyScopes.size === 0}
                >
                  {isCreatingKey ? (
                    <>
                      <Loader2 className="h-4 w-4 mr-2 animate-spin" />
                      作成中...
                    </>
                  ) : (
                    "キーを作成"
                  )}
                </Button>
              </DialogFooter>
            </div>
          )}
        </DialogContent>
      </Dialog>

      {/* Git Workspace */}
      <GitWorkspaceCard />
    </div>
  );
}

function GitWorkspaceCard() {
  const [repo, setRepo] = useState("");
  const [token, setToken] = useState("");
  const [branch, setBranch] = useState("main");
  const [isTesting, setIsTesting] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    // Load from server
    fetch(withBasePath("/api/vexa/user/workspace-git")).then(async (r) => {
      // GET doesn't exist — load from user profile data instead
    }).catch(() => {});
    // Also check localStorage as fallback
    try {
      const git = JSON.parse(localStorage.getItem("vexa-browser-git") || "{}");
      if (git.repo) {
        setRepo(git.repo);
        setToken(git.token || "");
        setBranch(git.branch || "main");
        setSaved(true);
      }
    } catch {}
  }, []);

  async function handleSave() {
    setIsSaving(true);
    try {
      const response = await fetch(withBasePath("/api/vexa/user/workspace-git"), {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ repo, token, branch }),
      });
      if (!response.ok) throw new Error(await response.text());
      // Also save to localStorage for the join modal to read
      localStorage.setItem("vexa-browser-git", JSON.stringify({ repo, token, branch }));
      setSaved(true);
      toast.success("Gitワークスペースを保存しました");
    } catch (error) {
      toast.error("保存に失敗しました: " + (error as Error).message);
    } finally {
      setIsSaving(false);
    }
  }

  async function handleClear() {
    try {
      await fetch(withBasePath("/api/vexa/user/workspace-git"), { method: "DELETE" });
      localStorage.removeItem("vexa-browser-git");
      setRepo("");
      setToken("");
      setBranch("main");
      setSaved(false);
      toast.success("Gitワークスペースを削除しました");
    } catch {
      toast.error("削除に失敗しました");
    }
  }

  async function handleTest() {
    setIsTesting(true);
    try {
      const repoPath = repo.replace("https://github.com/", "").replace(".git", "");
      const response = await fetch(`https://api.github.com/repos/${repoPath}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (response.ok) {
        toast.success("接続できました。リポジトリにアクセスできます");
      } else if (response.status === 404) {
        toast.error("リポジトリが見つかりません。URLとトークン権限を確認してください");
      } else {
        toast.error(`GitHub APIエラー: ${response.status}`);
      }
    } catch (error) {
      toast.error("接続に失敗しました: " + (error as Error).message);
    } finally {
      setIsTesting(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <GitBranch className="h-5 w-5" />
          Gitワークスペース
          {saved && repo && <Check className="h-4 w-4 text-green-500" />}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-muted-foreground">
          ブラウザセッションのワークスペースファイルを同期するGitHubリポジトリを設定します。対象リポジトリだけに権限を絞ったPATを使ってください。
        </p>
        <div className="space-y-3">
          <div className="space-y-1">
            <Label className="text-xs">リポジトリURL</Label>
            <Input
              placeholder="https://github.com/you/workspace.git"
              value={repo}
              onChange={(e) => { setRepo(e.target.value); setSaved(false); }}
            />
          </div>
          {repo && (
            <>
              <div className="space-y-1">
                <Label className="text-xs">個人アクセストークン</Label>
                <Input
                  placeholder="github_pat_..."
                  type="password"
                  value={token}
                  onChange={(e) => { setToken(e.target.value); setSaved(false); }}
                />
              </div>
              <div className="space-y-1">
                <Label className="text-xs">ブランチ</Label>
                <Input
                  placeholder="main"
                  value={branch}
                  onChange={(e) => { setBranch(e.target.value); setSaved(false); }}
                />
              </div>
            </>
          )}
        </div>
        <div className="flex gap-2">
          <Button size="sm" onClick={handleSave} disabled={!repo || isSaving}>
            {isSaving ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : null}
            保存
          </Button>
          {repo && token && (
            <Button size="sm" variant="outline" onClick={handleTest} disabled={isTesting}>
              {isTesting ? <Loader2 className="h-3 w-3 animate-spin mr-1" /> : <RefreshCw className="h-3 w-3 mr-1" />}
              接続確認
            </Button>
          )}
          {saved && repo && (
            <Button size="sm" variant="ghost" onClick={handleClear}>
              削除
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
