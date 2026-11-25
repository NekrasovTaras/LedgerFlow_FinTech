import { createRoot } from 'react-dom/client'
import { App } from './app/App'
import './styles.css'

document.documentElement.dataset.theme = localStorage.getItem('ledgerflow-theme') ?? 'light'
createRoot(document.getElementById('root')!).render(<App />)
