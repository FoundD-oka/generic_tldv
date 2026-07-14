"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { BookOpenText, Loader2, Pencil, Plus, Save, Trash2, X } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { vexaAPI, type TranscriptionDictionaryTerm } from "@/lib/api";

export default function DictionaryPage() {
  const [terms, setTerms] = useState<TranscriptionDictionaryTerm[]>([]);
  const [limit, setLimit] = useState(200);
  const [term, setTerm] = useState("");
  const [reading, setReading] = useState("");
  const [editingId, setEditingId] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);

  const load = useCallback(async () => {
    try {
      const data = await vexaAPI.getTranscriptionDictionary();
      setTerms(data.terms);
      setLimit(data.limit);
    } catch (error) {
      toast.error("辞書の読み込みに失敗しました", { description: (error as Error).message });
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const resetForm = () => {
    setTerm("");
    setReading("");
    setEditingId(null);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!term.trim()) return;
    setIsSaving(true);
    try {
      if (editingId !== null) {
        await vexaAPI.updateTranscriptionDictionaryTerm(editingId, { term, reading: reading || null });
        toast.success("辞書を更新しました");
      } else {
        await vexaAPI.createTranscriptionDictionaryTerm({ term, reading: reading || undefined });
        toast.success("辞書に追加しました", { description: "次回のGemini文字起こしから反映されます" });
      }
      resetForm();
      await load();
    } catch (error) {
      toast.error("辞書の保存に失敗しました", { description: (error as Error).message });
    } finally {
      setIsSaving(false);
    }
  };

  const startEdit = (item: TranscriptionDictionaryTerm) => {
    setEditingId(item.id);
    setTerm(item.term);
    setReading(item.reading || "");
  };

  return (
    <div className="mx-auto w-full max-w-4xl space-y-6 p-6">
      <div>
        <h1 className="flex items-center gap-2 text-3xl font-bold tracking-tight">
          <BookOpenText className="h-7 w-7" />文字起こし辞書
        </h1>
        <p className="mt-2 text-muted-foreground">
          人名・会社名・専門用語を登録すると、次のGemini会議後文字起こしで語彙ヒントとして使われます。
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>{editingId === null ? "語句を追加" : "語句を編集"}</CardTitle>
          <CardDescription>読みは省略可能です。辞書内容は命令としては扱われません。</CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={submit} className="grid gap-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
            <div className="space-y-2"><Label htmlFor="term">表記</Label><Input id="term" maxLength={100} value={term} onChange={(e) => setTerm(e.target.value)} placeholder="例: Bonginkan" /></div>
            <div className="space-y-2"><Label htmlFor="reading">読み（任意）</Label><Input id="reading" maxLength={100} value={reading} onChange={(e) => setReading(e.target.value)} placeholder="例: ボンギンカン" /></div>
            <div className="flex gap-2">
              <Button type="submit" disabled={isSaving || !term.trim()}>{isSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : editingId === null ? <Plus className="h-4 w-4" /> : <Save className="h-4 w-4" />}{editingId === null ? "追加" : "保存"}</Button>
              {editingId !== null && <Button type="button" variant="outline" size="icon" aria-label="編集をキャンセル" onClick={resetForm}><X className="h-4 w-4" /></Button>}
            </div>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>登録済み {terms.length} / {limit}</CardTitle><CardDescription>無効にした語句は削除せず保存されますが、Geminiへは渡されません。</CardDescription></CardHeader>
        <CardContent className="space-y-3">
          {isLoading ? <div className="flex items-center gap-2 text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />読み込み中...</div> : terms.length === 0 ? <p className="text-sm text-muted-foreground">まだ語句はありません。</p> : terms.map((item) => (
            <div key={item.id} className="flex items-center gap-3 rounded-lg border p-3">
              <Switch checked={item.enabled} onCheckedChange={async (enabled) => { await vexaAPI.updateTranscriptionDictionaryTerm(item.id, { enabled }); await load(); }} aria-label={`${item.term}の有効状態`} />
              <div className="min-w-0 flex-1"><p className="font-medium">{item.term}</p>{item.reading && <p className="text-sm text-muted-foreground">{item.reading}</p>}</div>
              <Button variant="ghost" size="icon" aria-label={`${item.term}を編集`} onClick={() => startEdit(item)}><Pencil className="h-4 w-4" /></Button>
              <Button variant="ghost" size="icon" aria-label={`${item.term}を削除`} className="text-destructive" onClick={async () => { if (!window.confirm(`「${item.term}」を削除しますか？`)) return; await vexaAPI.deleteTranscriptionDictionaryTerm(item.id); await load(); }}><Trash2 className="h-4 w-4" /></Button>
            </div>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
