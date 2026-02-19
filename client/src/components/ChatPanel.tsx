/**
 * AI Chat panel — interactive chat with SSE streaming, conversation history,
 * and markdown rendering powered by Streamdown.
 */
import { useState, useRef, useEffect, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Chip, Spinner, TextArea } from "@heroui/react";
import {
  Send,
  Bot,
  User,
  X,
  Sparkles,
  Plus,
  MessageSquare,
  Clock,
  ChevronLeft,
  Copy,
  RefreshCw,
} from "lucide-react";
import { Streamdown } from "streamdown";
import { code } from "@streamdown/code";
import "streamdown/styles.css";

import { format } from "date-fns";
import {
  sendChatMessage,
  fetchConversations,
  fetchConversation,
} from "../api/client";
import type { ChatConversation, Anomaly } from "../types";

interface ChatPanelProps {
  anomalyId?: string;
  anomalies?: Anomaly[];
  onClose?: () => void;
}

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
}

type PanelView = "chat" | "history";

export function ChatPanel({ anomalyId, anomalies = [], onClose }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [view, setView] = useState<PanelView>("chat");
  const [contextAnomalyIds, setContextAnomalyIds] = useState<string[]>(
    anomalyId ? [anomalyId] : []
  );

  // Helper to look up anomaly details by ID
  const getAnomalyInfo = (id: string) =>
    anomalies.find((a) => a.id === id);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const queryClient = useQueryClient();

  // Track external anomalyId changes (from "Ask AI About This" button)
  const prevAnomalyIdRef = useRef(anomalyId);

  useEffect(() => {
    if (anomalyId && anomalyId !== prevAnomalyIdRef.current) {
      prevAnomalyIdRef.current = anomalyId;
      // Add to context list if not already present
      setContextAnomalyIds((prev) =>
        prev.includes(anomalyId) ? prev : [...prev, anomalyId]
      );
    }
  }, [anomalyId]);

  const removeContextAnomaly = (id: string) => {
    setContextAnomalyIds((prev) => prev.filter((a) => a !== id));
  };

  useEffect(() => {
    if (view === "chat") {
      inputRef.current?.focus();
    }
  }, [view]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Fetch conversation history
  const { data: conversations = [], isLoading: loadingHistory } = useQuery({
    queryKey: ["chat-conversations"],
    queryFn: fetchConversations,
    refetchInterval: 30000,
  });

  const handleSend = useCallback(
    async (overrideMessage?: string) => {
      const msg = overrideMessage || input.trim();
      if (!msg || isStreaming) return;

      setInput("");
      setMessages((prev) => [...prev, { role: "user", content: msg }]);
      setIsStreaming(true);

      // Add empty assistant message that we'll stream into
      setMessages((prev) => [...prev, { role: "assistant", content: "" }]);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const stream = sendChatMessage({
          message: msg,
          anomaly_id: contextAnomalyIds[0],
          conversation_id: conversationId || undefined,
          signal: controller.signal,
        });

        for await (const chunk of stream) {
          if (controller.signal.aborted) break;
          if (chunk.type === "chunk" && chunk.content) {
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              updated[lastIdx] = {
                ...updated[lastIdx],
                content: updated[lastIdx].content + chunk.content,
              };
              return updated;
            });
          }
          if (chunk.type === "done" && chunk.conversation_id) {
            setConversationId(chunk.conversation_id);
            queryClient.invalidateQueries({
              queryKey: ["chat-conversations"],
            });
          }
          if (chunk.type === "error") {
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              updated[lastIdx] = {
                ...updated[lastIdx],
                content:
                  updated[lastIdx].content +
                  `\n\n> **Error:** ${chunk.content}`,
              };
              return updated;
            });
          }
        }
      } catch (error) {
        if (!controller.signal.aborted) {
          setMessages((prev) => {
            const updated = [...prev];
            const lastIdx = updated.length - 1;
            updated[lastIdx] = {
              ...updated[lastIdx],
              content: `> **Error:** ${error instanceof Error ? error.message : "Unknown error"}`,
            };
            return updated;
          });
        }
      } finally {
        setIsStreaming(false);
        abortRef.current = null;
      }
    },
    [input, isStreaming, contextAnomalyIds, conversationId, queryClient]
  );

  const handleStop = useCallback(() => {
    abortRef.current?.abort();
    setIsStreaming(false);
  }, []);

  const handleNewChat = useCallback(() => {
    if (isStreaming) {
      abortRef.current?.abort();
      setIsStreaming(false);
    }
    setMessages([]);
    setConversationId(null);
    setContextAnomalyIds(anomalyId ? [anomalyId] : []);
    setView("chat");
  }, [anomalyId, isStreaming]);

  const handleLoadConversation = useCallback(
    async (conv: ChatConversation) => {
      try {
        const full = await fetchConversation(conv.id);
        setMessages(
          full.messages.map((m) => ({
            role: m.role as "user" | "assistant",
            content: m.content,
          }))
        );
        setConversationId(conv.id);
        setContextAnomalyIds(conv.anomaly_id ? [conv.anomaly_id] : []);
        setView("chat");
      } catch {
        setView("chat");
      }
    },
    []
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const hasContext = contextAnomalyIds.length > 0;
  const quickQuestions = hasContext
    ? [
      "Analyze this anomaly — what caused it?",
      "What code changes might be related?",
      "How can I mitigate this issue?",
      "Is this a false positive?",
    ]
    : [
      "What anomalies have been detected?",
      "Which service has the most issues?",
      "Are there any cascading failures?",
      "Summarize the system health",
    ];

  // ── History View ──────────────────────────────────────────────────

  if (view === "history") {
    return (
      <div className="flex flex-col h-full">
        <div className="flex items-center justify-between px-4 py-3 border-b border-border">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setView("chat")}
              className="p-1 rounded-lg hover:bg-surface-secondary transition-colors"
            >
              <ChevronLeft size={16} className="text-muted" />
            </button>
            <h3 className="text-sm font-semibold text-foreground">
              Chat History
            </h3>
          </div>
          {onClose && (
            <button
              onClick={onClose}
              className="p-1 rounded-lg hover:bg-surface-secondary transition-colors"
            >
              <X size={16} className="text-muted" />
            </button>
          )}
        </div>

        <div className="flex-1 overflow-y-auto">
          {loadingHistory ? (
            <div className="flex items-center justify-center h-32">
              <Spinner size="sm" />
            </div>
          ) : conversations.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-full gap-3 text-center px-4">
              <MessageSquare size={32} className="text-muted" />
              <p className="text-sm text-muted">No conversations yet</p>
              <Button
                size="sm"
                variant="outline"
                onPress={() => setView("chat")}
              >
                Start a conversation
              </Button>
            </div>
          ) : (
            <div className="divide-y divide-border">
              {conversations.map((conv) => {
                const firstUserMsg = conv.messages?.find(
                  (m) => m.role === "user"
                );
                const preview =
                  firstUserMsg?.content?.slice(0, 80) || "Empty conversation";

                return (
                  <button
                    key={conv.id}
                    onClick={() => handleLoadConversation(conv)}
                    className={`w-full text-left px-4 py-3 hover:bg-surface-secondary transition-colors ${conv.id === conversationId ? "bg-surface-secondary" : ""
                      }`}
                  >
                    <div className="flex items-start gap-2">
                      <MessageSquare
                        size={14}
                        className="text-muted mt-0.5 shrink-0"
                      />
                      <div className="min-w-0 flex-1">
                        <p className="text-sm text-foreground truncate">
                          {preview}
                          {preview.length >= 80 ? "..." : ""}
                        </p>
                        <div className="flex items-center gap-2 mt-1">
                          <span className="text-xs text-muted flex items-center gap-1">
                            <Clock size={10} />
                            {format(
                              new Date(conv.created_at),
                              "MMM d, HH:mm"
                            )}
                          </span>
                          <span className="text-xs text-muted">
                            {conv.messages?.length || 0} msgs
                          </span>
                          {conv.anomaly_id && (
                            <Chip size="sm" variant="soft" color="accent">
                              anomaly
                            </Chip>
                          )}
                        </div>
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="border-t border-border p-3">
          <Button
            size="sm"
            variant="primary"
            className="w-full"
            onPress={handleNewChat}
          >
            <Plus size={14} />
            New Conversation
          </Button>
        </div>
      </div>
    );
  }

  // ── Chat View ─────────────────────────────────────────────────────

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-border">
        <div className="flex items-center gap-2">
          <Sparkles size={18} className="text-primary" />
          <h3 className="text-sm font-semibold text-foreground">
            AI Assistant
          </h3>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={handleNewChat}
            title="New conversation"
            className="p-1.5 rounded-lg hover:bg-surface-secondary transition-colors"
          >
            <Plus size={14} className="text-muted" />
          </button>
          <button
            onClick={() => setView("history")}
            title="View history"
            className="p-1.5 rounded-lg hover:bg-surface-secondary transition-colors"
          >
            <Clock size={14} className="text-muted" />
          </button>
          {onClose && (
            <button
              onClick={onClose}
              className="p-1.5 rounded-lg hover:bg-surface-secondary transition-colors"
            >
              <X size={14} className="text-muted" />
            </button>
          )}
        </div>
      </div>

      {/* Anomaly context badges */}
      {contextAnomalyIds.length > 0 && (
        <div className="px-4 py-2 border-b border-border bg-surface-secondary/50">
          <div className="flex items-start gap-2 flex-wrap">
            <span className="text-xs text-muted mt-1 shrink-0">Context:</span>
            <div className="flex flex-wrap gap-1.5 flex-1 min-w-0">
              {contextAnomalyIds.map((id) => {
                const info = getAnomalyInfo(id);
                const chipColor =
                  info?.severity === "critical"
                    ? "danger"
                    : info?.severity === "warning"
                      ? "warning"
                      : "accent";
                return (
                  <span key={id} className="inline-flex items-center gap-0.5">
                    <Chip size="sm" variant="soft" color={chipColor}>
                      {info
                        ? `${info.service_name} / ${info.metric_name} (${info.confidence_score >= 0 ? (info.confidence_score * 100).toFixed(0) + "%" : "—"})`
                        : `Anomaly ${id.slice(0, 8)}`}
                    </Chip>
                    <button
                      onClick={() => removeContextAnomaly(id)}
                      className="p-0.5 rounded hover:bg-surface-secondary transition-colors"
                      title="Remove from context"
                    >
                      <X size={10} className="text-muted" />
                    </button>
                  </span>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-4 text-center">
            <div className="p-3 rounded-2xl bg-primary/10">
              <Bot size={28} className="text-primary" />
            </div>
            <div>
              <p className="text-sm font-medium text-foreground">
                {hasContext
                  ? "Ask about this anomaly"
                  : "Ask me anything"}
              </p>
              <p className="text-xs text-muted mt-1 max-w-70">
                {hasContext
                  ? "I have context about this anomaly — ask about root causes, impact, or fixes"
                  : "I can analyze metrics, correlate with code changes, and suggest fixes"}
              </p>
            </div>
            <div className="flex flex-col gap-2 w-full max-w-xs">
              {quickQuestions.map((q) => (
                <button
                  key={q}
                  onClick={() => handleSend(q)}
                  disabled={isStreaming}
                  className="text-xs text-left px-3 py-2 rounded-lg border border-border hover:bg-surface-secondary transition-colors text-foreground disabled:opacity-50"
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex gap-2.5 ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            {msg.role === "assistant" && (
              <div className="shrink-0 p-1.5 rounded-lg bg-primary/10 h-fit mt-0.5">
                <Bot size={14} className="text-primary" />
              </div>
            )}
            <div className="flex flex-col gap-1 max-w-[90%]">
              <div
                className={`rounded-2xl px-3.5 py-2.5 text-sm overflow-hidden ${
                  msg.role === "user"
                    ? "bg-primary text-primary-foreground rounded-br-md"
                    : "border text-foreground rounded-bl-md chat-assistant-msg"
                }`}
              >
                {msg.role === "assistant" ? (
                  msg.content ? (
                    <Streamdown
                      plugins={{ code }}
                      shikiTheme={["catppuccin-latte", "catppuccin-mocha"]}
                      isAnimating={isStreaming && i === messages.length - 1}
                    >
                      {msg.content}
                    </Streamdown>
                  ) : (
                    <span className="shimmer shimmer-invert text-foreground/60 text-xs">
                      Thinking...
                    </span>
                  )
                ) : (
                  <p className="whitespace-pre-wrap">{msg.content}</p>
                )}
              </div>
              {msg.role === "assistant" && msg.content && !isStreaming && (
                <div className="flex items-center gap-1 ml-1">
                  <button
                    onClick={() => navigator.clipboard.writeText(msg.content)}
                    className="p-1 rounded hover:bg-surface-secondary transition-colors text-muted hover:text-foreground"
                    title="Copy"
                  >
                    <Copy size={12} />
                  </button>
                  {i === messages.length - 1 && (
                    <button
                      onClick={() => {
                        const lastUserMsg = [...messages].reverse().find((m) => m.role === "user");
                        if (lastUserMsg) {
                          setMessages((prev) => prev.slice(0, -1));
                          handleSend(lastUserMsg.content);
                        }
                      }}
                      className="p-1 rounded hover:bg-surface-secondary transition-colors text-muted hover:text-foreground"
                      title="Retry"
                    >
                      <RefreshCw size={12} />
                    </button>
                  )}
                </div>
              )}
            </div>
            {msg.role === "user" && (
              <div className="shrink-0 p-1.5 rounded-lg bg-surface-secondary h-fit mt-0.5">
                <User size={14} className="text-muted" />
              </div>
            )}
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="border-t border-border px-4 py-3">
        {isStreaming && (
          <div className="flex justify-center mb-2">
            <Button size="sm" variant="outline" onPress={handleStop}>
              Stop generating
            </Button>
          </div>
        )}
        <div className="flex items-end gap-2">
          <TextArea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              hasContext
                ? "Ask about this anomaly..."
                : "Ask about your metrics..."
            }
            rows={1}
            fullWidth
            className="flex-1 resize-none rounded-xl border border-border bg-surface px-3 py-2 text-sm text-foreground placeholder:text-muted focus:outline-none focus:ring-2 focus:ring-primary/40 max-h-32"
            disabled={isStreaming}
          />
          <Button
            isIconOnly
            variant="primary"
            size="sm"
            isDisabled={!input.trim() || isStreaming}
            onPress={() => handleSend()}
            className="shrink-0"
          >
            <Send size={14} />
          </Button>
        </div>
        {conversationId && (
          <p className="text-[10px] text-muted mt-1.5 text-center">
            Conversation {conversationId.slice(0, 8)} &middot;{" "}
            {messages.filter((m) => m.role === "user").length} messages
          </p>
        )}
      </div>
    </div>
  );
}
