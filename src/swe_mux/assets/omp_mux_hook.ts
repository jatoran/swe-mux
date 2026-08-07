import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

type HookContextLike = {
  cwd: string;
  sessionManager: {
    getSessionId(): string;
    getSessionFile(): string | undefined;
  };
};

type HookEnvelope = {
  event: string;
  source: "omp-extension";
  sequence: number;
  payload: Record<string, unknown>;
};

const DELIVERY_ATTEMPTS = 3;
const DELIVERY_TIMEOUT_MS = 1000;

function safePayload(value: Record<string, unknown>): Record<string, unknown> {
  const seen = new WeakSet<object>();
  const encoded = JSON.stringify(value, (_key, item: unknown) => {
    if (typeof item === "bigint") return item.toString();
    if (item && typeof item === "object") {
      if (seen.has(item)) return "[circular]";
      seen.add(item);
    }
    return item;
  });
  return JSON.parse(encoded) as Record<string, unknown>;
}

function sessionPayload(ctx: HookContextLike): Record<string, unknown> {
  return {
    session_id: ctx.sessionManager.getSessionId(),
    transcript_path: ctx.sessionManager.getSessionFile(),
    cwd: ctx.cwd,
  };
}

function contentText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((item): item is { type?: string; text?: string } => Boolean(item && typeof item === "object"))
    .filter((item) => item.type === "text" && typeof item.text === "string")
    .map((item) => item.text)
    .join("\n");
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function sweMuxHook(pi: ExtensionAPI): void {
  const url = process.env.MUX_HOOK_URL;
  const secret = process.env.MUX_HOOK_SECRET;
  let nextSequence = 0;
  let deliveryTail = Promise.resolve();

  async function post(envelope: HookEnvelope): Promise<void> {
    if (!url || !secret) return;
    for (let attempt = 0; attempt < DELIVERY_ATTEMPTS; attempt += 1) {
      try {
        const response = await fetch(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-Mux-Hook-Secret": secret,
          },
          body: JSON.stringify(envelope),
          signal: AbortSignal.timeout(DELIVERY_TIMEOUT_MS),
        });
        if (response.ok || response.status === 410) return;
        if (response.status < 500 && response.status !== 429) return;
      } catch {
        // A daemon reload is expected to break one attempt while the PTY survives.
      }
      if (attempt + 1 < DELIVERY_ATTEMPTS) await delay(50 * 2 ** attempt);
    }
    pi.logger.warn(`swe-mux hook delivery failed for ${envelope.event}`);
  }

  function emit(event: string, payload: Record<string, unknown>): Promise<void> {
    const envelope: HookEnvelope = {
      event,
      source: "omp-extension",
      sequence: ++nextSequence,
      payload: safePayload(payload),
    };
    const delivery = deliveryTail.then(() => post(envelope));
    deliveryTail = delivery.catch(() => undefined);
    return delivery;
  }

  pi.on("session_start", (_event, ctx) =>
    emit("SessionStart", { ...sessionPayload(ctx), source: "startup", omp_event: "session_start" }),
  );
  pi.on("before_agent_start", (event, ctx) =>
    emit("UserPromptSubmit", {
      ...sessionPayload(ctx),
      prompt: event.prompt,
      omp_event: "before_agent_start",
    }),
  );
  pi.on("turn_start", (event, ctx) =>
    emit("turn_started", {
      ...sessionPayload(ctx),
      turn_index: event.turnIndex,
      timestamp: event.timestamp,
      omp_event: "turn_start",
    }),
  );
  pi.on("turn_end", (event, ctx) =>
    emit("turn_ended", {
      ...sessionPayload(ctx),
      turn_index: event.turnIndex,
      root_completion: false,
      omp_event: "turn_end",
    }),
  );
  pi.on("agent_start", (_event, ctx) =>
    emit("task_started", { ...sessionPayload(ctx), omp_event: "agent_start" }),
  );
  pi.on("agent_end", (event, ctx) =>
    emit("task_complete", {
      ...sessionPayload(ctx),
      will_continue: event.willContinue === true,
      omp_event: "agent_end",
    }),
  );
  pi.on("tool_call", (event, ctx) =>
    emit("PreToolUse", {
      ...sessionPayload(ctx),
      tool_name: event.toolName,
      tool_use_id: event.toolCallId,
      tool_input: event.input,
      omp_event: "tool_call",
    }),
  );
  pi.on("tool_result", (event, ctx) =>
    emit(event.isError ? "PostToolUseFailure" : "PostToolUse", {
      ...sessionPayload(ctx),
      tool_name: event.toolName,
      tool_use_id: event.toolCallId,
      is_error: event.isError,
      result: contentText(event.content),
      omp_event: "tool_result",
    }),
  );
  pi.on("tool_approval_requested", (event, ctx) =>
    emit("PermissionRequest", {
      ...sessionPayload(ctx),
      tool_name: event.toolName,
      tool_use_id: event.toolCallId,
      reason: event.reason,
      approval_mode: event.approvalMode,
      omp_event: "tool_approval_requested",
    }),
  );
  pi.on("tool_approval_resolved", (event, ctx) =>
    emit("approval_resolved", {
      ...sessionPayload(ctx),
      tool_name: event.toolName,
      tool_use_id: event.toolCallId,
      approved: event.approved,
      reason: event.reason,
      omp_event: "tool_approval_resolved",
    }),
  );
  pi.on("session_compact", (event, ctx) =>
    emit("context_compacted", {
      ...sessionPayload(ctx),
      compaction_id: event.compactionEntry.id,
      tokens_before: event.compactionEntry.tokensBefore,
      omp_event: "session_compact",
    }),
  );
  pi.on("session_switch", (event, ctx) =>
    emit("SessionStart", {
      ...sessionPayload(ctx),
      source: event.reason,
      previous_session_file: event.previousSessionFile,
      omp_event: "session_switch",
    }),
  );
  pi.on("session_branch", (event, ctx) =>
    emit("SessionStart", {
      ...sessionPayload(ctx),
      source: "branch",
      previous_session_file: event.previousSessionFile,
      omp_event: "session_branch",
    }),
  );
  pi.on("session_shutdown", (_event, ctx) =>
    emit("SessionEnd", {
      ...sessionPayload(ctx),
      reason: "shutdown",
      omp_event: "session_shutdown",
    }),
  );
}
