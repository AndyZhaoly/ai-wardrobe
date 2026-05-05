'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import Image from 'next/image';
import { useSession } from 'next-auth/react';
import { Upload, Send, Loader2, RefreshCw, ShoppingBag, CheckCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { Badge } from '@/components/ui/badge';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Alert, AlertDescription } from '@/components/ui/alert';
import { Separator } from '@/components/ui/separator';
import { api, setAccessToken } from '@/lib/api';

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

interface GarmentUpload {
  listing_id: string;
  cropped_image_url: string | null;
}

interface ListingDraft {
  listing_title: string | null;
  listing_description: string | null;
  listing_price_usd: number | null;
  original_price_cny: number | null;
}

export default function ResellPage() {
  const { data: session } = useSession();

  const [upload, setUpload] = useState<GarmentUpload | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [history, setHistory] = useState<object[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [listing, setListing] = useState<ListingDraft | null>(null);
  const [posted, setPosted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);
  const chatBottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if ((session as any)?.accessToken) setAccessToken((session as any).accessToken);
  }, [session]);

  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleUpload = useCallback(async (file: File) => {
    setUploading(true);
    setError(null);
    setUpload(null);
    setMessages([]);
    setHistory([]);
    setListing(null);
    setPosted(false);

    try {
      const formData = new FormData();
      formData.append('file', file);
      const data: GarmentUpload = await api.postForm('/resale/garment', formData);
      setUpload(data);
      await sendMessage('主人刚刚上传了一件闲置衣物照片，请识别品牌和估价。', data.listing_id, []);
    } catch (err: any) {
      setError(err.message || '上传失败，请重试');
    } finally {
      setUploading(false);
    }
  }, []);

  const sendMessage = useCallback(
    async (text: string, listingId?: string, currentHistory?: object[]) => {
      const lid = listingId ?? upload?.listing_id;
      const hist = currentHistory ?? history;
      if (!lid) return;

      const userMsg: Message = { role: 'user', content: text };
      setMessages((prev) => [...prev, userMsg]);
      setSending(true);

      try {
        const response = await fetch(
          `${process.env.NEXT_PUBLIC_API_URL ?? ''}/api/v1/resale/chat`,
          {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${(session as any)?.accessToken ?? ''}`,
            },
            body: JSON.stringify({ listing_id: lid, message: text, history: hist }),
          }
        );

        if (!response.ok) throw new Error('Chat request failed');

        const reader = response.body!.getReader();
        const decoder = new TextDecoder();
        let assistantText = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          for (const line of decoder.decode(value).split('\n')) {
            if (!line.startsWith('data: ')) continue;
            try {
              const event = JSON.parse(line.slice(6));
              if (event.type === 'text') {
                assistantText += event.delta;
                setMessages((prev) => {
                  const copy = [...prev];
                  const last = copy[copy.length - 1];
                  if (last?.role === 'assistant') copy[copy.length - 1] = { role: 'assistant', content: assistantText };
                  else copy.push({ role: 'assistant', content: assistantText });
                  return copy;
                });
              } else if (event.type === 'tool_result') {
                handleToolResult(event.name, event.result);
              } else if (event.type === 'done') {
                setHistory(event.messages ?? [...hist, { role: 'user', content: text }]);
              }
            } catch { /* ignore */ }
          }
        }
      } catch (err: any) {
        setError(err.message || '发送失败');
      } finally {
        setSending(false);
      }
    },
    [upload, history, session]
  );

  const handleToolResult = (toolName: string, result: any) => {
    if (toolName === 'generate_poshmark_listing' && result.title) {
      setListing({
        listing_title: result.title,
        listing_description: result.description,
        listing_price_usd: null,
        original_price_cny: null,
      });
    }
    if (toolName === 'post_to_poshmark' && result.status === 'queued') {
      setPosted(true);
    }
  };

  const handleSend = () => {
    if (!input.trim() || sending) return;
    const text = input;
    setInput('');
    sendMessage(text);
  };

  const handleReset = () => {
    setUpload(null);
    setMessages([]);
    setHistory([]);
    setListing(null);
    setPosted(false);
    setError(null);
  };

  return (
    <div className="flex h-full flex-col gap-4 p-4 lg:p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">闲置变现管家</h1>
          <p className="text-muted-foreground text-sm">上传闲置衣物，小镜帮你生成 Poshmark 文案并自动挂单</p>
        </div>
        {upload && (
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

      {posted && (
        <Alert>
          <CheckCircle className="h-4 w-4" />
          <AlertDescription>Poshmark 发布任务已提交，正在后台处理中。</AlertDescription>
        </Alert>
      )}

      <div className="grid flex-1 grid-cols-1 gap-4 overflow-hidden lg:grid-cols-2">
        {/* Left panel */}
        <div className="flex flex-col gap-4 overflow-y-auto">
          {/* Upload */}
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">📦 上传衣物</CardTitle>
            </CardHeader>
            <CardContent>
              {!upload ? (
                <div
                  className="flex h-48 cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-muted-foreground/30 hover:border-primary/50 transition-colors"
                  onClick={() => fileInputRef.current?.click()}
                >
                  {uploading ? (
                    <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
                  ) : (
                    <>
                      <Upload className="h-10 w-10 text-muted-foreground" />
                      <p className="mt-2 text-sm text-muted-foreground">点击上传衣物照片</p>
                      <p className="text-xs text-muted-foreground">支持 JPG / PNG / WEBP</p>
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
                <div className="space-y-2">
                  <p className="text-xs text-muted-foreground">AI 裁剪结果</p>
                  {upload.cropped_image_url && (
                    <Image
                      src={upload.cropped_image_url}
                      alt="衣物"
                      width={300}
                      height={300}
                      className="w-full max-h-64 rounded-md object-contain bg-muted"
                    />
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          {/* Generated listing */}
          {listing && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base flex items-center gap-2">
                  <ShoppingBag className="h-4 w-4" />
                  Poshmark 文案
                  <Badge variant="secondary">草稿</Badge>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                {listing.listing_title && (
                  <div>
                    <p className="text-xs font-medium text-muted-foreground mb-1">标题</p>
                    <p className="text-sm font-semibold">{listing.listing_title}</p>
                  </div>
                )}
                {listing.listing_description && (
                  <>
                    <Separator />
                    <div>
                      <p className="text-xs font-medium text-muted-foreground mb-1">描述</p>
                      <p className="text-sm whitespace-pre-wrap text-muted-foreground">
                        {listing.listing_description}
                      </p>
                    </div>
                  </>
                )}
              </CardContent>
            </Card>
          )}
        </div>

        {/* Right panel: chat */}
        <Card className="flex flex-col overflow-hidden">
          <CardHeader className="pb-2 border-b">
            <CardTitle className="text-base flex items-center gap-2">
              🤵 小镜
              {upload && <Badge variant="outline" className="text-xs font-normal">在线</Badge>}
            </CardTitle>
          </CardHeader>
          <ScrollArea className="flex-1 p-4">
            {messages.length === 0 && !upload && (
              <div className="flex h-40 items-center justify-center text-center">
                <p className="text-sm text-muted-foreground">上传衣物照片，小镜帮您变现～</p>
              </div>
            )}
            <div className="space-y-3">
              {messages.map((msg, i) => (
                <div key={i} className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}>
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
                placeholder={upload ? '和小镜说点什么…' : '请先上传衣物照片'}
                disabled={!upload || sending}
                className="flex-1"
              />
              <Button
                size="icon"
                onClick={handleSend}
                disabled={!upload || !input.trim() || sending}
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
