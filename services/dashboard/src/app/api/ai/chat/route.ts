import { streamText } from "ai";
import { createOpenAI } from "@ai-sdk/openai";
import { createAnthropic } from "@ai-sdk/anthropic";
import { buildKabosuTranscriptSystemPrompt } from "@/lib/kabosu-persona";
import { redactSecrets } from "@/lib/redact-secrets";

export const runtime = "nodejs";

const MAX_CONTEXT_CHARS = 24000;

// Parse AI_MODEL env var format: "provider/model" (e.g., "openai/gpt-4o")
function parseAIModel(): { provider: string; model: string } | null {
  const aiModel = process.env.AI_MODEL;
  if (!aiModel) return null;

  const [provider, ...modelParts] = aiModel.split("/");
  const model = modelParts.join("/"); // Handle models with / in name (e.g., "openrouter/openai/gpt-4o")

  if (!provider || !model) return null;

  return { provider: provider.toLowerCase(), model };
}

function getModel() {
  const config = parseAIModel();
  if (!config) {
    throw new Error("AI not configured. Set AI_MODEL environment variable.");
  }

  const apiKey = process.env.AI_API_KEY;
  const baseUrl = process.env.AI_BASE_URL;
  const apiVersion = process.env.AI_API_VERSION;
  const { provider, model } = config;

  switch (provider) {
    case "azure": {
      if (!apiKey) throw new Error("AI_API_KEY is required for Azure OpenAI");
      if (!baseUrl) throw new Error("AI_BASE_URL is required for Azure OpenAI");
      const azureBaseUrl = baseUrl.replace(/\/$/, "");

      const azure = createOpenAI({
        apiKey,
        baseURL: azureBaseUrl,
        fetch: (url, options) => {
          const requestUrl = url instanceof Request ? new URL(url.url) : new URL(url);
          if (apiVersion && !requestUrl.searchParams.has("api-version")) {
            requestUrl.searchParams.set("api-version", apiVersion);
          }
          const headers = new Headers((url instanceof Request ? url.headers : options?.headers) || {});
          if (!headers.has("api-key")) {
            headers.set("api-key", apiKey);
          }
          if (headers.has("authorization")) {
            headers.delete("authorization");
          }
          if (url instanceof Request) {
            return fetch(new Request(requestUrl.toString(), { ...url, headers }));
          }
          return fetch(requestUrl.toString(), { ...options, headers });
        },
      });
      return azure(model);
    }

    case "openai": {
      if (!apiKey) throw new Error("AI_API_KEY is required for OpenAI");
      const openai = createOpenAI({
        apiKey,
        baseURL: baseUrl || "https://api.openai.com/v1",
      });
      return openai(model);
    }

    case "anthropic": {
      if (!apiKey) throw new Error("AI_API_KEY is required for Anthropic");
      const anthropic = createAnthropic({
        apiKey,
      });
      return anthropic(model);
    }

    case "groq": {
      if (!apiKey) throw new Error("AI_API_KEY is required for Groq");
      const groq = createOpenAI({
        apiKey,
        baseURL: baseUrl || "https://api.groq.com/openai/v1",
      });
      return groq(model);
    }

    case "openrouter": {
      if (!apiKey) throw new Error("AI_API_KEY is required for OpenRouter");
      const openrouter = createOpenAI({
        apiKey,
        baseURL: baseUrl || "https://openrouter.ai/api/v1",
      });
      return openrouter(model);
    }

    case "ollama":
    case "local":
    case "custom": {
      // For local/custom providers, API key may not be needed
      const custom = createOpenAI({
        apiKey: apiKey || "not-needed",
        baseURL: baseUrl || "http://localhost:11434/v1",
      });
      return custom(model);
    }

    default: {
      // Treat unknown providers as OpenAI-compatible with custom base URL
      if (!baseUrl) {
        throw new Error(`Unknown provider "${provider}". Set AI_BASE_URL for custom providers.`);
      }
      const customProvider = createOpenAI({
        apiKey: apiKey || "not-needed",
        baseURL: baseUrl,
      });
      return customProvider(model);
    }
  }
}

interface UIMessagePart {
  type: string;
  text?: string;
}

interface UIMessage {
  role: "user" | "assistant" | "system";
  content?: string;
  parts?: UIMessagePart[];
}

interface ChatRequest {
  messages: UIMessage[];
  context?: string;
  meeting?: {
    platform: string;
    nativeId: string;
    meetingId?: string | number;
  };
}

interface AssistantContextPayload {
  meeting?: {
    title?: string;
    native_meeting_id?: string;
    platform?: string;
    participants?: string[];
  };
  latest_segments?: Array<{ speaker?: string; text?: string }>;
  chat_messages?: Array<{ sender?: string; text?: string }>;
  shared_urls?: string[];
}

function sanitizeTranscriptContext(context: string): string {
  const clipped = context.slice(-MAX_CONTEXT_CHARS);
  return redactSecrets(clipped);
}

function assistantContextToText(context: AssistantContextPayload): string {
  const meeting = context.meeting || {};
  const lines: string[] = [];
  lines.push(`会議: ${meeting.title || meeting.native_meeting_id || ""}`);
  lines.push(`プラットフォーム: ${meeting.platform || ""}`);
  if (Array.isArray(meeting.participants) && meeting.participants.length > 0) {
    lines.push(`参加者: ${meeting.participants.join(", ")}`);
  }
  lines.push("");
  lines.push("文字起こし:");
  for (const segment of context.latest_segments || []) {
    const speaker = segment.speaker || "Unknown";
    const text = segment.text || "";
    if (text) lines.push(`[${speaker}] ${text}`);
  }
  if (Array.isArray(context.chat_messages) && context.chat_messages.length > 0) {
    lines.push("");
    lines.push("会議チャット:");
    for (const message of context.chat_messages) {
      const sender = message.sender || "Unknown";
      const text = message.text || "";
      if (text) lines.push(`[${sender}] ${text}`);
    }
  }
  if (Array.isArray(context.shared_urls) && context.shared_urls.length > 0) {
    lines.push("");
    lines.push("共有URL:");
    for (const url of context.shared_urls) {
      lines.push(`- ${url}`);
    }
  }
  return lines.join("\n").trim();
}

async function resolveTranscriptContext(request: Request, body: ChatRequest): Promise<string> {
  if (!body.meeting?.platform || !body.meeting.nativeId) {
    return body.context || "";
  }

  const assistantContextUrl = new URL(
    `/api/vexa/meetings/${encodeURIComponent(body.meeting.platform)}/${encodeURIComponent(body.meeting.nativeId)}/assistant-context`,
    request.url
  );
  if (body.meeting.meetingId != null) {
    assistantContextUrl.searchParams.set("meeting_id", String(body.meeting.meetingId));
  }

  try {
    const response = await fetch(assistantContextUrl, {
      headers: {
        cookie: request.headers.get("cookie") || "",
      },
      cache: "no-store",
    });
    if (!response.ok) {
      throw new Error(`assistant-context failed: ${response.status}`);
    }
    return assistantContextToText(await response.json());
  } catch (error) {
    console.warn("[AI Chat] Falling back to client-provided context:", error);
    return body.context || "";
  }
}

// Convert UI messages (with parts) to model messages (with content)
function convertMessages(messages: UIMessage[]): Array<{ role: "user" | "assistant"; content: string }> {
  return messages
    .filter(m => m.role === "user" || m.role === "assistant")
    .map(m => {
      let content = "";

      // If message has parts array (UI message format)
      if (m.parts && Array.isArray(m.parts)) {
        content = m.parts
          .filter(part => part.type === "text" && part.text)
          .map(part => part.text!)
          .join("");
      }
      // If message has content string (model message format)
      else if (m.content) {
        content = m.content;
      }

      return {
        role: m.role as "user" | "assistant",
        content,
      };
    })
    .filter(m => m.content.length > 0);
}

export async function POST(request: Request) {
  try {
    // Check if AI is configured
    if (!process.env.AI_MODEL) {
      return new Response(JSON.stringify({ error: "AI is not configured on this instance" }), {
        status: 503,
        headers: { "Content-Type": "application/json" },
      });
    }

    const body: ChatRequest = await request.json();
    const { messages } = body;
    const context = await resolveTranscriptContext(request, body);

    // Build the full system prompt with Kabosu persona before transcript context.
    const systemPrompt = buildKabosuTranscriptSystemPrompt(sanitizeTranscriptContext(context || ""));

    const model = getModel();

    // Convert UI messages to model messages
    const modelMessages = convertMessages(messages);

    if (modelMessages.length === 0) {
      return new Response(JSON.stringify({ error: "No valid messages to process" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      });
    }

    const result = streamText({
      model,
      system: systemPrompt,
      messages: modelMessages,
      onError({ error }) {
        console.error("AI streaming error:", error);
      },
    });

    return result.toUIMessageStreamResponse();
  } catch (error) {
    console.error("Agent API error:", error);
    const message = error instanceof Error ? error.message : "Unknown error";
    return new Response(JSON.stringify({ error: message }), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }
}
