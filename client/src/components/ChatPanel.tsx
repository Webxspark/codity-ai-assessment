/**
 * AI Chat panel — interactive chat with SSE streaming, tied to anomalies.
 */
import { useState, useRef, useEffect, useCallback } from "react";
import { Button, Spinner } from "@heroui/react";
import { Send, Bot, User, X, Sparkles } from "lucide-react";
import ReactMarkdown from "react-markdown";
import { sendChatMessage } from "../api/client";

interface ChatPanelProps {
  anomalyId?: string;
  onClose?: () => void;
}

interface ChatMsg {
  role: "user" | "assistant";
  content: string;
}

export function ChatPanel({ anomalyId, onClose }: ChatPanelProps) {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [isStreaming, setIsStreaming] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Auto-ask about newly selected anomaly
  useEffect(() => {
    if (anomalyId && messages.length === 0) {
      handleSend(
        "Analyze this anomaly. Explain why it was detected, what likely caused it, and suggest actionable fixes."
      );
    }
  }, [anomalyId]);

  const handleSend = useCallback(
    async (overrideMessage?: string) => {
      const msg = overrideMessage || input.trim();
      if (!msg || isStreaming) return;

      setInput("");
      setMessages((prev) => [...prev, { role: "user", content: msg }]);
      setIsStreaming(true);

      // Add empty assistant message that we'll stream into
      setMessages((prev) => [...prev, { role: "assistant", content: "" }]);

      try {
        const stream = sendChatMessage({
          message: msg,
          anomaly_id: anomalyId,
          conversation_id: conversationId || undefined,
        });

        for await (const chunk of stream) {
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
          }
          if (chunk.type === "error") {
            setMessages((prev) => {
              const updated = [...prev];
              const lastIdx = updated.length - 1;
              updated[lastIdx] = {
                ...updated[lastIdx],
                content:
                  updated[lastIdx].content +
                  `\n\n⚠️ Error: ${chunk.content}`,
              };
              return updated;
            });
          }
        }
      } catch (error) {
        setMessages((prev) => {
          const updated = [...prev];
          const lastIdx = updated.length - 1;
          updated[lastIdx] = {
            ...updated[lastIdx],
            content: `⚠️ Failed to get response: ${error instanceof Error ? error.message : "Unknown error"}`,
          };
          return updated;
        });
      } finally {
        setIsStreaming(false);
      }
    },
    [input, isStreaming, anomalyId, conversationId]
  );

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const quickQuestions = [
    "Why did this anomaly happen?",
    "What code changes could have caused this?",
    "How can I mitigate this issue?",
    "Compare this with the baseline behavior",
  ];

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
        {onClose && (
          <button
            onClick={onClose}
            className="p-1 rounded-lg hover:bg-surface-secondary transition-colors"
          >
            <X size={16} className="text-muted" />
          </button>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full gap-4 text-center">
            <Bot size={40} className="text-muted" />
            <div>
              <p className="text-sm font-medium text-foreground">
                Ask me about anomalies
              </p>
              <p className="text-xs text-muted mt-1">
                I can analyze metrics, correlate with code changes, and suggest
                fixes
              </p>
            </div>
            <div className="flex flex-col gap-2 w-full max-w-xs">
              {quickQuestions.map((q) => (
                <Button
                  key={q}
                  size="sm"
                  variant="outline"
                  className="text-xs justify-start"
                  onPress={() => handleSend(q)}
                >
                  {q}
                </Button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, i) => (
          <div
            key={i}
            className={`flex gap-2 ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            {msg.role === "assistant" && (
              <div className="shrink-0 p-1.5 rounded-lg bg-primary/10 h-fit">
                <Bot size={14} className="text-primary" />
              </div>
            )}
            <div
              className={`max-w-[85%] rounded-xl px-3 py-2 text-sm ${
                msg.role === "user"
                  ? "bg-primary text-primary-foreground"
                  : "bg-surface-secondary text-foreground"
              }`}
            >
              {msg.role === "assistant" ? (
                <div className="prose prose-sm dark:prose-invert max-w-none [&>*:first-child]:mt-0 [&>*:last-child]:mb-0">
                  <ReactMarkdown>{msg.content}</ReactMarkdown>
                  {isStreaming && i === messages.length - 1 && (
                    <span className="inline-block w-1.5 h-4 bg-primary animate-pulse ml-0.5" />
                  )}
                </div>
              ) : (
                <p>{msg.content}</p>
              )}
            </div>
            {msg.role === "user" && (
              <div className="shrink-0 p-1.5 rounded-lg bg-surface-secondary h-fit">
                <User size={14} className="text-muted" />
              </div>
            )}
          </div>
        ))}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="border-t border-border px-4 py-3">
        <div className="flex items-end gap-2">
          <textarea
            ref={inputRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about this anomaly..."
            rows={1}
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
            {isStreaming ? <Spinner size="sm" /> : <Send size={14} />}
          </Button>
        </div>
      </div>
    </div>
  );
}
