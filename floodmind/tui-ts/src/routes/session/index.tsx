/**
 * FloodMind TUI — Session View
 *
 * Copied from OpenCode's routes/session/index.tsx, adapted for Ink/React.
 * Component tree: scrollbox → For(messages) → UserMsg | AssistantMsg | ToolMsg
 */

import React, { useState, useRef, useEffect, createContext, useContext, useCallback, useMemo } from "react"
import { Box, Text, Static } from "ink"
import { useSDK, type MessageInfo } from "../../context/sdk"

// ── Colors (OpenCode theme) ─────────────────────────────────

const theme = {
  text: "#e0e0e0", textMuted: "#666666",
  background: "#1a1a2e", backgroundPanel: "#16213e",
  backgroundElement: "#0f3460", border: "#333355",
  accent: "#81c784", primary: "#4fc3f7",
  warning: "#ffa726", error: "#ef5350",
}

// ── Icons ───────────────────────────────────────────────────

const ICONS: Record<string, string> = {
  bash: "$", read: "→", write: "←", edit: "←",
  glob: "✱", grep: "✱", webfetch: "%", websearch: "◈",
  task: "│", question: "?", skill: "→",
  apply_patch: "%", todowrite: "⚙",
}

function toolIcon(name: string) { return ICONS[name?.toLowerCase()] ?? "⚙" }

// ── Session Context ─────────────────────────────────────────

type SessionCtx = { width: number }
const Ctx = createContext<SessionCtx>({ width: 80 })

// ── Session Component ───────────────────────────────────────

export function Session() {
  const sdk = useSDK()
  const msgs = sdk.messages()
  const streaming = sdk.streaming()
  const [input, setInput] = useState("")
  const [cursor, setCursor] = useState(true)
  const inputRef = useRef("")

  useEffect(() => {
    const t = setInterval(() => setCursor(c => !c), 530)
    return () => clearInterval(t)
  }, [])

  // Input handling
  const { stdin, setRawMode } = require("ink").useStdin?.() ?? { stdin: null }
  const streamingRef = useRef(streaming)
  streamingRef.current = streaming
  useEffect(() => {
    if (!stdin) return
    setRawMode?.(true)
    let escapeBuf = ""
    const onData = (data: Buffer) => {
      const raw = escapeBuf + data.toString()
      escapeBuf = ""
      if (raw.startsWith("\x1b") && raw.length < 3) { escapeBuf = raw; return }
      if (raw === "\r" || raw === "\n") {
        const t = inputRef.current.trim()
        if (t && !streamingRef.current) { sdk.send(t); setInput(""); inputRef.current = "" }
        return
      }
      if (raw === "\x1b" || raw.startsWith("\x1b[")) return
      if (raw === "\x7f" || raw === "\b") {
        const ns = inputRef.current.slice(0, -1)
        inputRef.current = ns; setInput(ns)
        return
      }
      if (raw === "\x03") { process.exit(0); return }
      if (raw >= " " && !raw.startsWith("\x1b")) {
        const ns = inputRef.current + raw
        inputRef.current = ns; setInput(ns)
      }
    }
    stdin.on("data", onData)
    return () => { stdin.removeAllListeners("data") }
  }, [])

  return (
    <Ctx.Provider value={{ width: 80 }}>
      <Box flexDirection="column" paddingLeft={2} paddingRight={2}>
        {/* Messages */}
        <Static items={msgs}>
          {(msg, i) => (
            <Msg
              key={i}
              msg={msg}
              last={i === msgs.length - 1 && msg.role === "assistant"}
            />
          )}
        </Static>

        {/* Input line */}
        <Box marginTop={1} flexDirection="column">
          <Box>
            <Text color={theme.textMuted}>┃  </Text>
            <Text color={input ? theme.text : theme.textMuted}>
              {input || "Ask anything..."}
            </Text>
            {!streaming && cursor && <Text color={theme.primary}>▊</Text>}
          </Box>
          <Text dimColor>  deepseek-v4-flash</Text>
        </Box>

        {/* Footer */}
        <Box marginTop={1}>
          <Text dimColor>  esc exit</Text>
        </Box>
      </Box>
    </Ctx.Provider>
  )
}

// ── Message Components ──────────────────────────────────────

function Msg({ msg, last }: { msg: MessageInfo; last: boolean }) {
  if (msg.role === "user") return <UserMsg msg={msg} />
  if (msg.role === "assistant") return <AssistantMsg msg={msg} last={last} />
  if (msg.role === "tool") return <ToolMsg msg={msg} />
  if (msg.role === "thought") return <ThoughtMsg msg={msg} />
  return null
}

function UserMsg({ msg }: { msg: MessageInfo }) {
  return (
    <Box marginTop={1} paddingLeft={0}>
      <Text color={theme.primary}>┃  </Text>
      <Text color={theme.text}>{msg.text}</Text>
    </Box>
  )
}

function AssistantMsg({ msg, last }: { msg: MessageInfo; last: boolean }) {
  const has = msg.text.trim().length > 0
  const done = msg.status === "completed"
  return (
    <Box flexDirection="column" marginTop={1}>
      <Box>
        <Text color={theme.accent}>┃  </Text>
        {has ? (
          <Text color={theme.text}>{msg.text.trim()}</Text>
        ) : (
          <Text color={theme.textMuted}>Thinking...</Text>
        )}
      </Box>
      {last && done && has && (
        <Box marginTop={1} paddingLeft={1}>
          <Text color={theme.accent}> ▣ </Text>
          <Text color={theme.text}>Assistant</Text>
          <Text dimColor> · deepseek-v4-flash</Text>
        </Box>
      )}
    </Box>
  )
}

function ToolMsg({ msg }: { msg: MessageInfo }) {
  const running = msg.status === "running"
  const icon = toolIcon(msg.toolName || "")
  return (
    <Box flexDirection="column" marginTop={1}>
      <Box paddingLeft={1}>
        <Text color={running ? theme.warning : theme.accent}>
          {icon} {msg.toolName}
        </Text>
      </Box>
      {!running && msg.text && msg.text !== "Running..." && (
        <Box paddingLeft={3}>
          <Text dimColor>{msg.text.slice(0, 300)}</Text>
        </Box>
      )}
    </Box>
  )
}

function ThoughtMsg({ msg }: { msg: MessageInfo }) {
  return (
    <Box paddingLeft={1} marginTop={1}>
      <Text color={theme.warning}>+ Thought</Text>
    </Box>
  )
}
