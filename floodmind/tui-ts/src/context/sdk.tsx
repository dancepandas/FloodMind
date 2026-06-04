/**
 * SDK Context — SSE connection to Python backend (React version)
 */

import React, { createContext, useContext, useState, useCallback, useRef } from "react"

const BACKEND_URL = process.env.FLOODMIND_API_URL || "http://127.0.0.1:13014"

export type MessageInfo = {
  role: "user" | "assistant" | "tool"
  text: string
  toolName?: string
  status?: "running" | "completed" | "error"
}

type SDK = {
  send: (message: string, sessionId?: string) => void
  messages: () => MessageInfo[]
  streaming: () => boolean
}

const Ctx = createContext<SDK | null>(null)

export function useSDK() {
  const ctx = useContext(Ctx)
  if (!ctx) throw new Error("useSDK must be used within SDKProvider")
  return ctx
}

export function SDKProvider({ children }: { children: React.ReactNode }) {
  const [messages, setMessages] = useState<MessageInfo[]>([])
  const [streaming, setStreaming] = useState(false)
  const msgsRef = useRef<MessageInfo[]>([])

  const send = useCallback(async (userInput: string, sessionId: string = "tui") => {
    // Add user message
    const newMsgs = [...msgsRef.current, { role: "user" as const, text: userInput }]
    msgsRef.current = newMsgs
    setMessages([...newMsgs])

    // Add assistant placeholder
    const withPlaceholder = [...msgsRef.current, { role: "assistant" as const, text: "", status: "running" as const }]
    msgsRef.current = withPlaceholder
    setMessages([...withPlaceholder])
    setStreaming(true)

    try {
      const resp = await fetch(`${BACKEND_URL}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: userInput, session_id: sessionId }),
      })

      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)

      const reader = resp.body!.getReader()
      const decoder = new TextDecoder()
      let buf = ""

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buf += decoder.decode(value, { stream: true })
        const lines = buf.split("\n")
        buf = lines.pop() || ""

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue
          const data = line.slice(6)
          if (data === '{"type":"__done__"}') continue
          try {
            const event = JSON.parse(data)

            if (event.type === "answer_delta") {
              const cur = [...msgsRef.current]
              const last = cur[cur.length - 1]
              if (last?.role === "assistant") {
                cur[cur.length - 1] = { ...last, text: last.text + (event.content || "") }
                msgsRef.current = cur
                setMessages([...cur])
              }
            } else if (event.type === "action_start" || event.type === "action_end") {
              const isDone = event.type === "action_end"
              let updated = false
              if (isDone) {
                const cur = [...msgsRef.current]
                for (let i = cur.length - 1; i >= 0; i--) {
                  if (cur[i].role === "tool" && cur[i].toolName === event.tool_name && cur[i].status === "running") {
                    cur[i] = { ...cur[i], text: (event.content || "").slice(0, 2000), toolName: `${event.tool_name} — done`, status: "completed" as const }
                    msgsRef.current = cur; setMessages([...cur]); updated = true
                    break
                  }
                }
              }
              if (!updated) {
                const cur = [...msgsRef.current, {
                  role: "tool" as const,
                  text: isDone ? (event.content || "").slice(0, 2000) : "Running...",
                  toolName: isDone ? `${event.tool_name} — done` : (event.tool_name || "?"),
                  status: isDone ? "completed" as const : "running" as const,
                }]
                msgsRef.current = cur
                setMessages([...cur])
              }
            } else if (event.type === "error") {
              const cur = [...msgsRef.current, {
                role: "assistant" as const,
                text: `Error: ${event.content}`,
                status: "error" as const,
              }]
              msgsRef.current = cur
              setMessages([...cur])
            }
          } catch (e) {
            console.error("[sdk] failed to parse SSE event:", data, e)
          }
      }
    } catch (err: any) {
      const cur = [...msgsRef.current]
      const last = cur[cur.length - 1]
      if (last?.role === "assistant") {
        cur[cur.length - 1] = { ...last, text: last.text + `\n\n[Error: ${err.message}]`, status: "error" }
      }
      msgsRef.current = cur
      setMessages([...cur])
    } finally {
      setStreaming(false)
      // Finalize
      const cur = [...msgsRef.current]
      const last = cur[cur.length - 1]
      if (last?.role === "assistant" && last.status === "running") {
        cur[cur.length - 1] = { ...last, status: "completed" }
        msgsRef.current = cur
        setMessages([...cur])
      }
    }
  }, [])

  const sdk: SDK = {
    send,
    messages: () => messages,
    streaming: () => streaming,
  }

  return <Ctx.Provider value={sdk}>{children}</Ctx.Provider>
}
