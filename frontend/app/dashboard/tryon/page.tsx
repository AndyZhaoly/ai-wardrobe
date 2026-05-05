'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import { useSession } from 'next-auth/react';
import { Camera, Send, Loader2, RefreshCw, Sparkles, ShoppingBag } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { api, setAccessToken } from '@/lib/api';

// ── types ─────────────────────────────────────────────────────────────────────

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

interface RecommendedItem {
  index: number;
  item_id: string;
  name: string;
  category: string;
  color: string;
  style: string;
  description: string;
  image_url: string;
}

interface TryonSession {
  session_id: string;
  upper_detected: boolean;
  lower_detected: boolean;
  upper_image_url: string | null;
  lower_image_url: string | null;
}

interface TryonStatus {
  session_id: string;
  status: string;
  result_image_url: string | null;
  error_message: string | null;
}

// ── component ─────────────────────────────────────────────────────────────────

export default function TryonPage() {
  const { data: session } = useSession();

  const [uploadedSession, setUploadedSession] = useState<TryonSession | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [history, setHistory] = useState<object[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [recommendations, setRecommendations] = useState<RecommendedItem[]>([]);
  const [tryonResult, setTryonResult] = useState<string | null>(null);
  const [pollingSessionId, setPollingSessionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const chatBottomRef = useRef<HTMLDivElement>(null);
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // sync auth token
  useEffect(() => {
    if ((session as any)?.accessToken) setAccessToken((session as any).accessToken);
  }, [session]);

  // auto-scroll chat
  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // polling for tryon result
  useEffect(() => {
    if (!pollingSessionId) return;
    pollIntervalRef.current = setInterval(async () => {
      try {
        const data: TryonStatus = await api.get(`/tryon/${pollingSessionId}`);
        if (data.status === 'done') {
          setTryonResult(data.result_image_url);
          setPollingSessionId(null);
          clearInterval(pollIntervalRef.current!);
        } else if (data.status === 'error') {
          setError(`试衣失败：${data.error_message}`);
          setPollingSessionId(null);
          clearInterval(pollIntervalRef.current!);
        }
      } catch {
        // ignore transient errors
      }
    }, 3000);
    return () => clearInterval(pollIntervalRef.current!);
  }, [pollingSessionId]);

  // ── handlers ───────────────────────────────────────────────────────────────

  const handleUpload = useCallback(async (file: File) => {
    setUploading(true);
    setError(null);
    setUploadedSession(null);
    setMessages([]);
    setHistory([]);
    setRecommendations([]);
    setTryonResult(null);

    try {
      const formData = new FormData();
      formData.append('file', file);
      const data: TryonSession = await api.postForm('/tryon/selfie', formData);
      setUploadedSession(data);

      // kick off agent greeting
      await sendMessage('主人刚刚上传了一张自拍照片。', data.session_id, []);
    } catch (err: any) {
      setError(err.message || '上传失败，请重试');
    } finally {
      setUploading(false);
    }
  }, []);

  const sendMessage = useCallback(
    async (text: string, sessionId?: string, currentHistory?: object[]) => {
      const sid = sessionId ?? uploadedSession?.session_id;
      const hist = currentHistory ?? history;
      if (!sid) return;

      const userMsg: Message = { role: 'user', content: text };
      setMessages((prev) => [...prev, userMsg]);
      setSending(true);

      const newHistory = [...hist, { role: 'user', content: text }];

      try {
        const response = await fetch(
          `${process.env.NEXT_PUBLIC_API_URL ?? ''}/api/v1/tryon/chat`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${(session as any)?.accessToken ?? ''}`,
            },
            body: JSON.stringify({ session_id: sid, message: text, history: hist }),
          }
        );

        if (!response.ok) throw new Error('Chat request failed');

        const reader = response.body!.getReader();
        const decoder = new TextDecoder();
        let assistantText = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          const chunk = decoder.decode(value);
          for (const line of chunk.split('\n')) {
            if (!line.startsWith('data: ')) continue;
            try {
              const event = JSON.parse(line.slice(6));
              if (event.type === 'text') {
                assistantText += event.delta;
                setMessages((prev) => {
                  const copy = [...prev];
                  const last = copy[copy.length - 1];
                  if (last?.role === 'assistant') {
                    copy[copy.length - 1] = { role: 'assistant', content: assistantText };
                  } else {
                    copy.push({ role: 'assistant', content: assistantText });
                  }
                  return copy;
                });
              } else if (event.type === 'tool_result') {
                handleToolResult(event.name, event.result);
              } else if (event.type === 'done') {
                setHistory(event.messages ?? newHistory);
              }
            } catch {
              // ignore malformed lines
            }
          }
        }
      } catch (err: any) {
        setError(err.message || '发送失败，请重试');
      } finally {
        setSending(false);
      }
    },
    [uploadedSession, history, session]
  );

  const handleToolResult = (toolName: string, result: any) => {
    if (toolName === 'show_recommendations' && result.items?.length) {
      setRecommendations(result.items);
    }
    if (toolName === 'trigger_virtual_tryon' && result.tryon_session_id) {
      setPollingSessionId(result.tryon_session_id);
    }
    if (toolName === 'try_all_lower' && result.session_ids?.length) {
      // poll the first one for now; a full gallery can be a future enhancement
      setPollingSessionId(result.session_ids[0]);
    }
  };

  const handleSend = () => {
    if (!input.trim() || sending) return;
    const text = input;
    setInput('');
    sendMessage(text);
  };

  const handleReset = () => {
    setUploadedSession(null);
    setMessages([]);
    setHistory([]);
    setRecommendations([]);
    setTryonResult(null);
    setPollingSessionId(null);
    setError(null);
    if (pollIntervalRef.current) clearInterval(pollIntervalRef.current);
  };

  // ── render ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex h-full flex-col gap-4 p-4 lg:p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">智能试衣间</h1>
          <p className="text-muted-foreground text-sm">上传自拍，小镜为你搭配今日穿搭</p>
        </div>
        {uploadedSession && (
          <Button variant="outline" size="sm" onClick={handleReset}>
            <RefreshCw className="mr-2 h-4 w-4" />
            重新开始
          </Button>
        )}
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="grid flex-1 grid-cols-1 gap-4 overflow-hidden lg:grid-cols-2">
        {/* ── Left panel ── */}
        <div className="flex flex-col gap-4 overflow-y-auto">
          {/* Selfie upload */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">📷 上传自拍</CardTitle>
            </CardHeader>
            <CardContent>
              {!uploadedSession ? (
                <div
                  className="flex h-48 cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-muted-foreground/30 hover:border-primary/50 transition-colors"
                  onClick={() => fileInputRef.current?.click()}
                >
                  {uploading ? (
                    <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                  ) : (
                    <>
                      <Camera className="h-10 w-10 text-muted-foreground" />
                      <p className="mt-2 text-sm text-muted-foreground">点击上传照片</p>
                    </>
                  )}
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/*"
                    className="hidden"
                    onChange={(e) => e.target.files?.[0] && handleUpload(e.target.files[0])}
                  />
                </div>
              ) : (
                <div className="grid grid-cols-2 gap-2">
                  {uploadedSession.upper_image_url && (
                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground">上衣识别</p>
                      <Image
                        src={uploadedSession.upper_image_url}
                        alt="上衣"
                        width={200}
                        height={200}
                        className="rounded-md object-contain bg-muted w-full h-32"
                      />
                    </div>
                  )}
                  {uploadedSession.lower_image_url && (
                    <div className="space-y-1">
                      <p className="text-xs text-muted-foreground">下装识别</p>
                      <Image
                        src={uploadedSession.lower_image_url}
                        alt="下装"
                        width={200}
                        height={200}
                        className="rounded-md object-contain bg-muted w-full h-32"
                      />
                    </div>
                  )}
                  {!uploadedSession.upper_detected && !uploadedSession.lower_detected && (
                    <p className="col-span-2 text-sm text-muted-foreground">
                      未检测到服装（分割服务可能未运行）
                    </p>
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Recommendations */}
          {recommendations.length > 0 && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">👗 推荐搭配</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="grid grid-cols-3 gap-2">
                  {recommendations.map((item) => (
                    <button
                      key={item.item_id}
                      className="group relative overflow-hidden rounded-md border hover:border-primary transition-colors text-left"
                      onClick={() => sendMessage(`我想试试第${item.index}件，${item.name}`)}
                    >
                      <div className="aspect-square bg-muted">
                        <Image
                          src={item.image_url}
                          alt={item.name}
                          width={150}
                          height={150}
                          className="h-full w-full object-cover"
                        />
                      </div>
                      <p className="truncate p-1 text-xs">{item.name}</p>
                      <Badge variant="secondary" className="absolute top-1 left-1 text-[10px]">
                        第{item.index}件
                      </Badge>
                    </button>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {/* Try-on result */}
          {(tryonResult || pollingSessionId) && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base flex items-center gap-2">
                  <Sparkles className="h-4 w-4" />
                  试衣效果
                </CardTitle>
              </CardHeader>
              <CardContent>
                {pollingSessionId && !tryonResult ? (
                  <div className="flex h-48 items-center justify-center">
                    <div className="text-center">
                      <Loader2 className="mx-auto h-8 w-8 animate-spin text-primary" />
                      <p className="mt-2 text-sm text-muted-foreground">小镜正在帮您试穿，请稍候…</p>
                    </div>
                  </div>
                ) : tryonResult ? (
                  <Image
                    src={tryonResult}
                    alt="试衣结果"
                    width={400}
                    height={600}
                    className="w-full rounded-md object-contain"
                  />
                ) : null}
              </CardContent>
            </Card>
          )}
        </div>

        {/* ── Right panel: chat ── */}
        <Card className="flex flex-col overflow-hidden">
          <CardHeader className="pb-2 border-b">
            <CardTitle className="text-base flex items-center gap-2">
              🤵 小镜
              {uploadedSession && (
                <Badge variant="outline" className="text-xs font-normal">
                  在线
                </Badge>
              )}
            </CardTitle>
          </CardHeader>
          <ScrollArea className="flex-1 p-4">
            {messages.length === 0 && !uploadedSession && (
              <div className="flex h-40 items-center justify-center text-center">
                <div>
                  <p className="text-sm text-muted-foreground">上传自拍照片，小镜会为您服务～</p>
                </div>
              </div>
            )}
            <div className="space-y-3">
              {messages.map((msg, i) => (
                <div
                  key={i}
                  className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                >
                  <div
                    className={`max-w-[85%] rounded-2xl px-4 py-2 text-sm whitespace-pre-wrap ${
                      msg.role === 'user'
                        ? 'bg-primary text-primary-foreground rounded-br-sm'
                        : 'bg-muted rounded-bl-sm'
                    }`}
                  >
                    {msg.content}
                  </div>
                </div>
              ))}
              {sending && (
                <div className="flex justify-start">
                  <div className="rounded-2xl rounded-bl-sm bg-muted px-4 py-2">
                    <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                  </div>
                </div>
              )}
            </div>
            <div ref={chatBottomRef} />
          </ScrollArea>
          <div className="border-t p-3">
            <div className="flex gap-2">
              <Input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && !e.shiftKey && handleSend()}
                placeholder={uploadedSession ? '和小镜说点什么…' : '请先上传自拍照片'}
                disabled={!uploadedSession || sending}
                className="flex-1"
              />
              <Button
                size="icon"
                onClick={handleSend}
                disabled={!uploadedSession || !input.trim() || sending}
              >
                <Send className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
