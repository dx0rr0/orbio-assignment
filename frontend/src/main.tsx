import { createRoot } from 'react-dom/client'

import App from './App'
import './index.css'

// No StrictMode: its double-mounted effects would open two sockets, and opening a socket
// starts a conversation on the server.
const root = document.getElementById('root')
if (!root) throw new Error('index.html has no #root element')
createRoot(root).render(<App />)
