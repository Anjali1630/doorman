import { BrowserRouter, Routes, Route } from 'react-router-dom'
import AppShell from './components/AppShell'
import ControlCenter from './pages/ControlCenter'
import BrowserInspector from './pages/BrowserInspector'
import Executions from './pages/Executions'
import Integrations from './pages/Integrations'
import Evaluation from './pages/Evaluation'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<ControlCenter />} />
          <Route path="inspector" element={<BrowserInspector />} />
          <Route path="executions" element={<Executions />} />
          <Route path="integrations" element={<Integrations />} />
          <Route path="evaluation" element={<Evaluation />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
