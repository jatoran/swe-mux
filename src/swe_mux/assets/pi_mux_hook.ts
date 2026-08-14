import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

/**
 * swe-mux lifecycle reporter for the upstream pi coding agent.
 *
 * Deliberately a sibling of `omp_mux_hook.ts` rather than a shared file: pi and
 * oh-my-pi forked, and their event vocabularies have drifted. pi has no
 * `session_switch`/`session_branch` (it has `session_before_switch` and
 * `session_before_tree`/`session_tree`) and, more importantly, **no approval
 * events at all** — pi runs a tool as soon as the model asks for it, and gating
 * is something a user extension adds on top of `tool_call`. So this emits no
 * `PermissionRequest`, and pi's descriptor does not claim `approval_needed`.
 */

type HookContextLike = {
  cwd: string;
  sessionManager: {
    getSessionId(): string;
    getSessionFile(): string | undefined;
  };
};

type HookEnvelope = {
  event: string;
  source: "pi-extension";
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

// Last identity read successfully, so an event fired against a stale ctx still
// names the conversation it belongs to instead of arriving anonymous.
let lastSessionId: string | undefined
let lastTranscriptPath: string | undefined
let lastCwd: string | undefined

function sessionPayload(ctx: HookContextLike): Record<string, unknown> {
  // pi invalidates a ctx across session replacement (`/new`, `/fork`,
  // `/resume`, `reload`) and *throws* on any later read of it. Compaction can
  // replace the session too, so events fire against a dead ctx in ordinary use
  // — measured against pi 0.74.2, where reading it unguarded took the whole
  // extension down with "This extension ctx is stale after session replacement
  // or reload". A lifecycle reporter must never be able to break the session it
  // reports on, so every read is guarded and falls back to the last good value.
  try {
    lastSessionId = ctx.sessionManager.getSessionId() ?? lastSessionId
    // pi writes no terminal breadcrumb, so this is the only authoritative
    // pane-to-conversation link mux gets. It must be on every envelope: a
    // `/new` or `/resume` replaces the file underneath a live pane.
    lastTranscriptPath = ctx.sessionManager.getSessionFile() ?? lastTranscriptPath
    lastCwd = ctx.cwd ?? lastCwd
  } catch {
    // Stale ctx. The cached identity is the honest answer: it names the
    // conversation this event belongs to, and the replacement announces itself
    // through the `session_start` that follows.
  }
  return {
    session_id: lastSessionId,
    transcript_path: lastTranscriptPath,
    cwd: lastCwd,
    ...(lastSessionId === undefined ? { identity_unavailable: true } : {}),
  };
}

function contentText(content: unknown): string {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter((item): item is { type?: string; text?: string } =>
      Boolean(item && typeof item === "object"),
    )
    .filter((item) => item.type === "text" && typeof item.text === "string")
    .map((item) => item.text)
    .join("\n");
}

function agentOutcome(messages: unknown): { outcome: string; stopReason?: string } {
  if (!Array.isArray(messages)) return { outcome: "completed" };
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index] as { role?: unknown; stopReason?: unknown } | undefined;
    if (!message || message.role !== "assistant" || typeof message.stopReason !== "string") continue;
    const stopReason = message.stopReason;
    if (["aborted", "cancelled", "canceled", "interrupted"].includes(stopReason)) {
      return { outcome: "interrupted", stopReason };
    }
    if (stopReason === "error" || stopReason === "length") {
      return { outcome: stopReason, stopReason };
    }
    return { outcome: "completed", stopReason };
  }
  return { outcome: "completed" };
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export default function sweMuxHook(pi: ExtensionAPI): void {
  const url = process.env.MUX_HOOK_URL;
  const secret = process.env.MUX_HOOK_SECRET;
  let nextSequence = 0;
  let nextRootTurn = 0;
  let activeRootTurnId: string | undefined;
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
  }

  /**
   * One monotonic sequence, delivered strictly in order.
   *
   * The extension runs in pi's own process, so unlike a shell hook it can
   * guarantee ordering against the transcript writes it describes. That is what
   * lets pi's descriptor declare `hook_ordering_guarantee = True`; chaining on
   * `deliveryTail` is what makes the claim true under retry.
   */
  function emit(event: string, payload: Record<string, unknown>): Promise<void> {
    const envelope: HookEnvelope = {
      event,
      source: "pi-extension",
      sequence: ++nextSequence,
      payload: safePayload(payload),
    };
    const delivery = deliveryTail.then(() => post(envelope));
    deliveryTail = delivery.catch(() => undefined);
    return delivery;
  }

  pi.on("session_start", (event, ctx) =>
    emit("SessionStart", {
      ...sessionPayload(ctx),
      source: event.reason ?? "startup",
      previous_session_file: event.previousSessionFile,
      pi_event: "session_start",
    }),
  );
  pi.on("before_agent_start", (event, ctx) =>
    emit("UserPromptSubmit", {
      ...sessionPayload(ctx),
      prompt: event.prompt,
      pi_event: "before_agent_start",
    }),
  );
  pi.on("turn_start", (event, ctx) =>
    emit("turn_started", {
      ...sessionPayload(ctx),
      turn_index: event.turnIndex,
      timestamp: event.timestamp,
      pi_event: "turn_start",
    }),
  );
  pi.on("turn_end", (event, ctx) =>
    emit("turn_ended", {
      ...sessionPayload(ctx),
      turn_index: event.turnIndex,
      // A pi turn ends between steps while the agent keeps working; only
      // `agent_end` closes the root run. Mirrors omp, whose milestone
      // `agent_end` semantics are the reason this flag exists.
      root_completion: false,
      pi_event: "turn_end",
    }),
  );
  pi.on("agent_start", (_event, ctx) => {
    activeRootTurnId = `pi-root-${++nextRootTurn}`;
    return emit("task_started", {
      ...sessionPayload(ctx),
      turn_id: activeRootTurnId,
      pi_event: "agent_start",
    });
  });
  pi.on("agent_end", (event, ctx) => {
    const terminal = agentOutcome((event as { messages?: unknown }).messages);
    const turnId = activeRootTurnId;
    activeRootTurnId = undefined;
    return emit("task_complete", {
      ...sessionPayload(ctx),
      will_continue:
        (event as { willContinue?: boolean }).willContinue === true,
      turn_id: turnId,
      outcome: terminal.outcome,
      stop_reason: terminal.stopReason,
      pi_event: "agent_end",
    });
  });
  pi.on("tool_call", (event, ctx) =>
    emit("PreToolUse", {
      ...sessionPayload(ctx),
      tool_name: event.toolName,
      tool_use_id: event.toolCallId,
      tool_input: event.input,
      pi_event: "tool_call",
    }),
  );
  pi.on("tool_result", (event, ctx) =>
    emit(event.isError ? "PostToolUseFailure" : "PostToolUse", {
      ...sessionPayload(ctx),
      tool_name: event.toolName,
      tool_use_id: event.toolCallId,
      is_error: event.isError,
      result: contentText(event.content),
      pi_event: "tool_result",
    }),
  );
  pi.on("session_compact", (event, ctx) =>
    emit("context_compacted", {
      ...sessionPayload(ctx),
      compaction_id: event.compactionEntry?.id,
      tokens_before: event.compactionEntry?.tokensBefore,
      pi_event: "session_compact",
    }),
  );
  // `session_before_switch` fires while the old conversation is still current,
  // so its payload still names the outgoing file. mux needs the *incoming* one,
  // which `session_start` reports a moment later with reason "new"/"resume".
  // Emitting here anyway gives the daemon the boundary before the file changes,
  // which is what stops a turn landing on the retired conversation.
  pi.on("session_before_switch", (event, ctx) =>
    emit("SessionStart", {
      ...sessionPayload(ctx),
      source: event.reason,
      target_session_file: event.targetSessionFile,
      pending: true,
      pi_event: "session_before_switch",
    }),
  );
  pi.on("session_tree", (event, ctx) =>
    emit("SessionStart", {
      ...sessionPayload(ctx),
      source: "branch",
      new_leaf_id: event.newLeafId,
      old_leaf_id: event.oldLeafId,
      pi_event: "session_tree",
    }),
  );
  pi.on("session_shutdown", (event, ctx) =>
    emit("SessionEnd", {
      ...sessionPayload(ctx),
      reason: event.reason ?? "shutdown",
      target_session_file: event.targetSessionFile,
      pi_event: "session_shutdown",
    }),
  );
}
