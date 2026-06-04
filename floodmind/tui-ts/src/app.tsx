/**
 * FloodMind TUI — Main App (OpenCode-style)
 *
 * Component tree: App → SDKProvider → Session → Messages + Input + Footer
 * Copied from OpenCode's app.tsx structure, adapted for Ink/React.
 */

import React from "react"
import { render } from "ink"
import { SDKProvider } from "./context/sdk"
import { Session } from "./routes/session"

function App() {
  return (
    <SDKProvider>
      <Session />
    </SDKProvider>
  )
}

render(<App />)
