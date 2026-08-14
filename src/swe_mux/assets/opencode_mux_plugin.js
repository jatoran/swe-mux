/**
 * swe-mux lifecycle reporter for opencode.
 *
 * Shipped as plain JavaScript, not TypeScript: opencode loads a plugin named by
 * absolute path straight from disk, and mux has no build step in that path.
 * Verified against opencode 1.18.16 (2026-08-10) — an `OPENCODE_CONFIG` file
 * listing this file's absolute path loads it and delivers the whole event bus,
 * while the user's own config layers keep loading and merging.
 *
 * One opencode process is one mux pane, and that process owns one server, so
 * every session the plugin sees belongs to this pane. `/new` mints a fresh
 * `ses_…` and arrives here as `session.created`, which is a CLI-reported
 * rollover in the same sense as Claude's SessionStart.
 */

const DELIVERY_ATTEMPTS = 3
const DELIVERY_TIMEOUT_MS = 1000

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function safePayload(value) {
  const seen = new WeakSet()
  return JSON.parse(
    JSON.stringify(value, (_key, item) => {
      if (typeof item === "bigint") return item.toString()
      if (item && typeof item === "object") {
        if (seen.has(item)) return "[circular]"
        seen.add(item)
      }
      return item
    }),
  )
}

function terminalOutcome(error) {
  const name = String(error?.name ?? error?.message ?? "error")
  return /abort|cancel|interrupt/i.test(name) ? "interrupted" : "error"
}

export const MuxHook = async ({ serverUrl, directory, worktree }) => {
  const url = process.env.MUX_HOOK_URL
  const secret = process.env.MUX_HOOK_SECRET
  let nextSequence = 0
  let nextRootTurn = 0
  let activeRootTurnId = null
  let deliveryTail = Promise.resolve()
  // The pane's current conversation. opencode reports a session id on nearly
  // every event, so this only backfills the few that do not carry one.
  let currentSession = null

  async function post(envelope) {
    if (!url || !secret) return
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
        })
        if (response.ok || response.status === 410) return
        if (response.status < 500 && response.status !== 429) return
      } catch {
        // A daemon reload breaks one attempt while the PTY survives.
      }
      if (attempt + 1 < DELIVERY_ATTEMPTS) await delay(50 * 2 ** attempt)
    }
  }

  /**
   * Sequence numbers are monotonic, but the descriptor still declares
   * `hook_ordering_guarantee = False`. The plugin is in-process with the server
   * yet posts over loopback HTTP, and nothing measured says the POST lands after
   * the store write it describes — so mux keeps arbitrating rather than trusting
   * this ordering.
   */
  function emit(event, sessionID, payload) {
    if (sessionID) currentSession = sessionID
    const envelope = {
      event,
      source: "opencode-plugin",
      sequence: ++nextSequence,
      payload: safePayload({
        session_id: sessionID || currentSession,
        cwd: directory,
        worktree,
        server_url: serverUrl ? String(serverUrl) : undefined,
        ...payload,
      }),
    }
    const delivery = deliveryTail.then(() => post(envelope))
    deliveryTail = delivery.catch(() => undefined)
    return delivery
  }

  return {
    event: async ({ event }) => {
      const type = event?.type
      const props = event?.properties ?? {}
      const sessionID = props.sessionID ?? props.info?.id ?? props.id

      switch (type) {
        case "session.created": {
          // `startup` means "a fresh process announcing itself" and is
          // deliberately refused as a conversation replacement; an in-place
          // replacement must report `new`. One opencode process is one pane, so
          // the first session.created is the pane starting and any later one is
          // /new replacing the conversation underneath it. Reporting `startup`
          // for both made every rollover land as
          // `foreign_conversation_hook_ignored`: the pane kept reporting the
          // retired conversation and its measurements as live.
          const replacing = currentSession !== null && currentSession !== sessionID
          activeRootTurnId = null
          return emit("SessionStart", sessionID, {
            source: replacing ? "new" : "startup",
            previous_session_id: replacing ? currentSession : undefined,
            opencode_event: type,
          })
        }
        case "session.status": {
          // {type:"idle"} | {type:"busy"} | {type:"retry",attempt,message,next}
          const status = props.status?.type
          if (status !== "busy") return
          activeRootTurnId ??= `opencode-root-${++nextRootTurn}`
          return emit("turn_started", sessionID, {
            status,
            turn_id: activeRootTurnId,
            opencode_event: type,
          })
        }
        case "session.idle": {
          // The one unambiguous root-completion signal opencode emits.
          const turnId = activeRootTurnId
          activeRootTurnId = null
          return emit("turn_ended", sessionID, {
            root_completion: true,
            turn_id: turnId,
            opencode_event: type,
          })
        }
        case "session.compacted":
          return emit("context_compacted", sessionID, { opencode_event: type })
        case "session.error": {
          const turnId = activeRootTurnId
          activeRootTurnId = null
          return emit("turn_ended", sessionID, {
            root_completion: true,
            turn_id: turnId,
            outcome: terminalOutcome(props.error),
            error: props.error?.name ?? "error",
            opencode_event: type,
          })
        }
        case "session.deleted":
          return emit("SessionEnd", sessionID, {
            reason: "deleted",
            opencode_event: type,
          })
        case "permission.updated":
          return emit("PermissionRequest", sessionID, {
            permission_id: props.id,
            tool_name: props.type ?? props.title,
            pattern: props.pattern,
            opencode_event: type,
          })
        case "permission.replied":
          return emit("approval_resolved", sessionID, {
            permission_id: props.permissionID,
            approved: props.response !== "reject",
            response: props.response,
            opencode_event: type,
          })
        case "server.instance.disposed":
          return emit("SessionEnd", sessionID, {
            reason: "server_disposed",
            opencode_event: type,
          })
        default:
          // The live bus carries more than the SDK's typed union (measured:
          // message.part.delta, plugin.added, catalog.updated,
          // reference.updated, integration.updated). Unknown types are dropped
          // rather than guessed at.
          return
      }
    },

    "chat.message": async (input) => {
      await emit("UserPromptSubmit", input?.sessionID, {
        agent: input?.agent,
        model: input?.model,
      })
    },

    "tool.execute.before": async (input) => {
      await emit("PreToolUse", input?.sessionID, {
        tool_name: input?.tool,
        tool_use_id: input?.callID,
      })
    },

    "tool.execute.after": async (input, output) => {
      await emit("PostToolUse", input?.sessionID, {
        tool_name: input?.tool,
        tool_use_id: input?.callID,
        title: output?.title,
      })
    },

    dispose: async () => {
      await emit("SessionEnd", currentSession, { reason: "shutdown" })
    },
  }
}

export default { id: "swe-mux", server: MuxHook }
