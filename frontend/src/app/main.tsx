import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'

const container = document.getElementById('root')
if (container === null) throw new Error('index.html is missing its #root element')

createRoot(container).render(
  <StrictMode>
    <h1>AD Robot</h1>
  </StrictMode>,
)
