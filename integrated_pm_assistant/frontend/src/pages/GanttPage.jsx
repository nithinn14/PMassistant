import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { BarChart3 } from 'lucide-react'
import { getProjectResults } from '../services/api'

const COLORS = [
    'bg-primary-500', 'bg-accent-violet', 'bg-accent-cyan', 'bg-accent-emerald',
    'bg-accent-amber', 'bg-accent-rose', 'bg-primary-400', 'bg-purple-500',
]

export default function GanttPage({ projectCtx }) {
    const [searchParams, setSearchParams] = useSearchParams()
    const projectParam = searchParams.get('project')
    const activeProject = projectParam || projectCtx?.projectName || ''

    const [data, setData] = useState(null)
    const [loading, setLoading] = useState(true)
    const [projectInput, setProjectInput] = useState(activeProject)

    async function loadData(name) {
        if (!name) return
        setLoading(true)
        try {
            const res = await getProjectResults(name)
            setData(res)
        } catch { /* ignore */ }
        setLoading(false)
    }

    useEffect(() => {
        if (activeProject) {
            setProjectInput(activeProject)
            loadData(activeProject)
        } else {
            setData(null)
            setLoading(false)
        }
    }, [activeProject])

    function handleManualLoad(name) {
        if (!name?.trim()) return
        const trimmed = name.trim()
        if (projectParam !== trimmed) {
            setSearchParams({ project: trimmed })
        } else {
            loadData(trimmed)
        }
    }

    if (!loading && !data) {
        return (
            <div className="empty-state animate-fade-in-up">
                <div className="empty-state-icon bg-gradient-to-br from-accent-cyan/10 to-accent-emerald/10">
                    <BarChart3 className="w-9 h-9 text-accent-cyan" />
                </div>
                <h2 className="text-2xl font-black tracking-tight mb-2">Gantt Chart</h2>
                <p className="text-surface-500 text-sm mb-6 max-w-sm leading-relaxed">
                    Enter a project name to view its schedule as a Gantt chart.
                </p>
                <div className="project-input-group">
                    <input type="text" placeholder="Project name..."
                        value={projectInput} onChange={(e) => setProjectInput(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleManualLoad(projectInput)}
                        className="border border-surface-200/80 dark:border-surface-700/50 bg-surface-50/50 dark:bg-surface-800/40 focus:outline-none focus:ring-2 focus:ring-primary-500/30"
                    />
                    <button onClick={() => handleManualLoad(projectInput)}
                        className="bg-gradient-to-r from-primary-600 to-accent-violet text-white shadow-xl shadow-primary-500/20 hover:shadow-primary-500/35">
                        Load
                    </button>
                </div>
            </div>
        )
    }

    if (loading) {
        return (
            <div className="min-h-screen flex items-center justify-center">
                <div className="w-10 h-10 border-3 border-primary-500/20 border-t-primary-500 rounded-full animate-spin" />
            </div>
        )
    }

    const schedule = data?.schedule || []
    if (!schedule.length) {
        return (
            <div className="page-container">
                <div className="page-header animate-fade-in-up">
                    <p className="page-subtitle">Gantt Chart</p>
                    <h1 className="page-title gradient-text">{data.project_name}</h1>
                </div>
                <div className="glass-card dash-card text-center py-16">
                    <p className="text-surface-500 text-sm font-semibold">No schedule data available for this project.</p>
                </div>
            </div>
        )
    }

    // Parse dates and calculate timeline
    const parsedSchedule = schedule.map((s, i) => {
        const start = new Date(s.start_date)
        const end = new Date(s.end_date)
        return { ...s, startDate: start, endDate: end, colorIdx: i % COLORS.length }
    }).filter(s => !isNaN(s.startDate) && !isNaN(s.endDate))

    if (!parsedSchedule.length) {
        return (
            <div className="page-container">
                <div className="page-header animate-fade-in-up">
                    <p className="page-subtitle">Gantt Chart</p>
                    <h1 className="page-title gradient-text">{data.project_name}</h1>
                </div>
                <div className="glass-card dash-card text-center py-16">
                    <p className="text-surface-500 text-sm font-semibold">Schedule dates could not be parsed.</p>
                </div>
            </div>
        )
    }

    const minDate = new Date(Math.min(...parsedSchedule.map(s => s.startDate)))
    const maxDate = new Date(Math.max(...parsedSchedule.map(s => s.endDate)))
    const totalDays = Math.max(1, (maxDate - minDate) / (1000 * 60 * 60 * 24))

    return (
        <div className="page-container space-y-6">
            <div className="page-header animate-fade-in-up">
                <p className="page-subtitle">Gantt Chart</p>
                <h1 className="page-title gradient-text">{data.project_name}</h1>
                <p className="text-surface-500 text-sm font-semibold mt-1">
                    {minDate.toLocaleDateString()} — {maxDate.toLocaleDateString()} · {parsedSchedule.length} tasks
                </p>
            </div>

            <div className="glass-card dash-card animate-fade-in-up overflow-x-auto" style={{ animationDelay: '100ms' }}>
                <div className="min-w-[800px]">
                    {parsedSchedule.map((s, i) => {
                        const leftPct = ((s.startDate - minDate) / (1000 * 60 * 60 * 24)) / totalDays * 100
                        const widthPct = Math.max(2, ((s.endDate - s.startDate) / (1000 * 60 * 60 * 24)) / totalDays * 100)
                        return (
                            <div key={i} className="flex items-center gap-4 py-2 group hover:bg-surface-800/20 rounded-lg px-2 transition-colors">
                                <div className="w-[200px] shrink-0 text-xs font-semibold truncate text-surface-300">
                                    {s.task_name || s.task}
                                </div>
                                <div className="flex-1 relative h-7 rounded-lg bg-surface-800/20">
                                    <div
                                        className={`absolute top-0.5 bottom-0.5 rounded-md ${COLORS[s.colorIdx]} opacity-80 group-hover:opacity-100 transition-opacity flex items-center px-2`}
                                        style={{ left: `${leftPct}%`, width: `${widthPct}%` }}
                                        title={`${s.assigned_empl || '?'}: ${s.startDate.toLocaleDateString()} — ${s.endDate.toLocaleDateString()}`}
                                    >
                                        <span className="text-[9px] font-bold text-white truncate">
                                            {s.assigned_empl || ''}
                                        </span>
                                    </div>
                                </div>
                            </div>
                        )
                    })}
                </div>
            </div>
        </div>
    )
}
