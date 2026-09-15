import { useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import SetupPage from './pages/SetupPage'
import ProcessingPage from './pages/ProcessingPage'
import DashboardPage from './pages/DashboardPage'
import ProjectsPage from './pages/ProjectsPage'
import TaskBoardPage from './pages/TaskBoardPage'
import ResourcesPage from './pages/ResourcesPage'
import MeetingsPage from './pages/MeetingsPage'
import GanttPage from './pages/GanttPage'
import NotificationsPage from './pages/NotificationsPage'

export default function App() {
    const [dark, setDark] = useState(true)
    const [projectCtx, setProjectCtx] = useState({
        jobId: null,
        projectName: '',
    })

    return (
        <BrowserRouter>
            <div className={dark ? 'dark' : ''}>
                <div className="flex h-screen bg-surface-50 text-surface-900 dark:bg-surface-950 dark:text-surface-100 transition-colors duration-300">
                    <Sidebar dark={dark} setDark={setDark} />
                    <main className="flex-1 overflow-y-auto">
                        <Routes>
                            <Route
                                path="/"
                                element={<SetupPage setProjectCtx={setProjectCtx} />}
                            />
                            <Route
                                path="/processing"
                                element={
                                    projectCtx.jobId ? (
                                        <ProcessingPage projectCtx={projectCtx} setProjectCtx={setProjectCtx} />
                                    ) : (
                                        <Navigate to="/" replace />
                                    )
                                }
                            />
                            <Route
                                path="/dashboard"
                                element={<DashboardPage projectCtx={projectCtx} />}
                            />
                            <Route
                                path="/projects"
                                element={<ProjectsPage />}
                            />
                            <Route
                                path="/tasks"
                                element={<TaskBoardPage projectCtx={projectCtx} />}
                            />
                            <Route
                                path="/resources"
                                element={<ResourcesPage projectCtx={projectCtx} />}
                            />
                            <Route
                                path="/meetings"
                                element={<MeetingsPage projectCtx={projectCtx} />}
                            />
                            <Route
                                path="/gantt"
                                element={<GanttPage projectCtx={projectCtx} />}
                            />
                            <Route
                                path="/notifications"
                                element={<NotificationsPage projectCtx={projectCtx} />}
                            />
                        </Routes>
                    </main>
                </div>
            </div>
        </BrowserRouter>
    )
}
