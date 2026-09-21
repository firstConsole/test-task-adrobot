import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

import './styles/index.css'

const container = document.getElementById('root')
if (container === null) throw new Error('index.html is missing its #root element')

createRoot(container).render(
  <StrictMode>
    <main className="mx-auto max-w-3xl p-8">
      <h1 className="text-2xl font-semibold tracking-tight">AD Robot</h1>
      <p className="text-muted-foreground mt-2 text-sm">
        Keitaro campaign builder and stream editor.
      </p>
    </main>
  </StrictMode>,
)
