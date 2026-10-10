import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import LoginGate from './LoginGate.tsx'

createRoot(document.getElementById('root')!).render(
  <LoginGate>
    <App />
  </LoginGate>,
)
