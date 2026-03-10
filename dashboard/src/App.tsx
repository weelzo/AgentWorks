import { Routes, Route, Navigate } from 'react-router'
import { Sidebar } from './components/Sidebar'
import { Header } from './components/Header'
import { AllRuns } from './views/AllRuns/AllRuns'
import { RunTimeline } from './views/RunTimeline/RunTimeline'
import { StateMachine } from './views/StateMachine/StateMachine'
import { CostDashboard } from './views/CostDashboard/CostDashboard'
import { ConversationThread } from './views/ConversationThread/ConversationThread'
import { RunComparison } from './views/RunComparison/RunComparison'

export default function App() {
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <Header />
        <main className="flex-1 overflow-y-auto bg-ctp-base">
          <Routes>
            <Route path="/runs" element={<AllRuns />} />
            <Route path="/timeline" element={<RunTimeline />} />
            <Route path="/states" element={<StateMachine />} />
            <Route path="/cost" element={<CostDashboard />} />
            <Route path="/conversation" element={<ConversationThread />} />
            <Route path="/compare" element={<RunComparison />} />
            <Route path="*" element={<Navigate to="/runs" replace />} />
          </Routes>
        </main>
      </div>
    </div>
  )
}
