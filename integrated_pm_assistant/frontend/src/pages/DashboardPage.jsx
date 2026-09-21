import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
    Download,
    FileJson,
    FileText,
    ListChecks,
    Users,
    CalendarDays,
    Bell,
    Video,
    CheckCircle2,
    XCircle,
    Clock,
    HelpCircle,
    ArrowUpRight,
    Sparkles,
    Hash,
    UserCheck,
    Calendar,
    TrendingUp,
    RefreshCw,
} from 'lucide-react'
import { getProjectResults, getDownloadUrl, syncMeetings } from '../services/api'

/* ─── Badge ─────────────────────────────────── */
function Badge({ children, color = 'gray' }) {
    const styles = {
        green: 'bg-accent-emerald/10 text-accent-emerald border-accent-emerald/15',
        red: 'bg-accent-rose/10 text-accent-rose border-accent-rose/15',
        orange: 'bg-accent-amber/10 text-accent-amber border-accent-amber/15',
        blue: 'bg-accent-cyan/10 text-accent-cyan border-accent-cyan/15',
        indigo: 'bg-primary-500/10 text-primary-500 border-primary-500/15',
        violet: 'bg-accent-violet/10 text-accent-violet border-accent-violet/15',
        gray: 'bg-surface-100 text-surface-500 dark:bg-surface-800 dark:text-surface-400 border-surface-200 dark:border-surface-700',
    }
    return (
        <span className={`inline-flex items-center px-2.5 py-1 rounded-lg text-[10px] font-bold tracking-wide border ${styles[color]}`}>
            {children}
        </span>
    )
}

/* ─── Stat Card ─────────────────────────────── */
function StatCard({ icon: Icon, label, value, gradient, glow, delay = 0 }) {
    return (
        <div
            className={`stat-card glass-card ${glow} animate-fade-in-up`}
            style={{ animationDelay: `${delay}ms` }}
        >
            <div className="flex items-center justify-between mb-3">
                <div className={`flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br ${gradient} shadow-lg`}>
                    <Icon className="w-5 h-5 text-white" />
                </div>
            </div>
            <p className="text-3xl font-black tracking-tight">{value}</p>
            <p className="text-xs font-semibold text-surface-400 mt-1 uppercase tracking-wider">{label}</p>
            <div className={`absolute top-0 right-0 w-24 h-24 rounded-full bg-gradient-to-br ${gradient} opacity-[0.06]`} style={{ transform: 'translate(30%,-30%)' }} />
        </div>
    )
}

/* ─── Section ──────────────────────────────── */
function Section({ icon: Icon, title, badge, children, delay = 0 }) {
    return (
        <div
            className="glass-card dash-card animate-fade-in-up"
            style={{ animationDelay: `${delay}ms` }}
        >
            <div className="flex items-center gap-3 mb-5">
                <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br from-primary-500/10 to-accent-violet/10">
                    <Icon className="w-5 h-5 text-primary-500" />
                </div>
                <div className="flex-1">
                    <h2 className="text-base font-extrabold tracking-tight">{title}</h2>
                </div>
                {badge}
            </div>
            {children}
        </div>
    )
}

/* ─── Main ─────────────────────────────────── */
export default function DashboardPage({ projectCtx }) {
    const [searchParams, setSearchParams] = useSearchParams()
    const projectParam = searchParams.get('project')
    const activeProject = projectParam || projectCtx?.projectName || ''

    const [data, setData] = useState(null)
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [ambiguousMatches, setAmbiguousMatches] = useState([])
    const [projectInput, setProjectInput] = useState(activeProject)
    const [syncing, setSyncing] = useState(false)

    async function handleSync() {
        if (!data?.project_name || syncing) return
        setSyncing(true)
        try {
            const meetingType = data.meeting_summary?.meeting_type || 'Kickoff'
            const updatedData = await syncMeetings(data.project_name, meetingType)
            setData(updatedData)
        } catch (err) {
            console.error('Sync failed:', err)
            alert('Failed to sync meetings. Please try again.')
        } finally {
            setSyncing(false)
        }
    }

    async function loadResults(name) {
        if (!name) return
        setLoading(true)
        setError(null)
        setAmbiguousMatches([])
        try {
            const res = await getProjectResults(name)
            if (res.ambiguous) {
                setAmbiguousMatches(res.matches || [])
                setData(null)
            } else {
                setData(res)
                if (res.project_name && res.project_name !== activeProject) {
                    setSearchParams({ project: res.project_name })
                    setProjectInput(res.project_name)
                }
            }
        } catch (err) {
            const msg = err.response?.data?.error || `No project found matching "${name}". Check the Projects page for available projects.`
            setError(msg)
            setData(null)
        } finally {
            setLoading(false)
        }
    }

    useEffect(() => {
        if (activeProject) {
            setProjectInput(activeProject)
            if (data?.project_name !== activeProject) {
                loadResults(activeProject)
            }
        } else {
            setData(null)
            setError(null)
            setAmbiguousMatches([])
            setLoading(false)
        }
    }, [activeProject])

    function handleManualLoad(name) {
        if (!name?.trim()) return
        const trimmed = name.trim()
        if (projectParam !== trimmed) {
            setSearchParams({ project: trimmed })
        } else {
            loadResults(trimmed)
        }
    }

    /* ── Empty state ──────── */
    if (!loading && !data) {
        return (
            <div className="min-h-screen flex flex-col items-center justify-center p-6">
                <div className="fixed inset-0 -z-10">
                    <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[600px] rounded-full bg-primary-500/5 blur-3xl" />
                </div>
                <div className="w-full max-w-md text-center animate-fade-in-up">
                    <div className="inline-flex items-center justify-center w-20 h-20 rounded-3xl bg-gradient-to-br from-primary-500/10 to-accent-violet/10 mb-5">
                        <Sparkles className="w-9 h-9 text-primary-500" />
                    </div>
                    <h2 className="text-2xl font-extrabold tracking-tight mb-2">Load a Project</h2>
                    <p className="text-surface-500 text-sm mb-7 leading-relaxed">
                        Enter a project name to view its generated results and analytics.
                    </p>
                    <div className="flex gap-2">
                        <input
                            id="load-project-input"
                            type="text"
                            placeholder="e.g. Company Knowledge Hub"
                            value={projectInput}
                            onChange={(e) => setProjectInput(e.target.value)}
                            onKeyDown={(e) => e.key === 'Enter' && handleManualLoad(projectInput)}
                            className="flex-1 rounded-2xl border border-surface-200/80 dark:border-surface-700/50 bg-surface-50/50 dark:bg-surface-800/40 px-5 py-3.5 text-sm font-medium focus:outline-none focus:ring-2 focus:ring-primary-500/30 transition-all"
                        />
                        <button
                            onClick={() => handleManualLoad(projectInput)}
                            className="px-6 py-3.5 rounded-2xl bg-gradient-to-r from-primary-600 to-accent-violet text-white font-bold text-sm shadow-xl shadow-primary-500/20 hover:shadow-primary-500/35 transition-all animate-gradient"
                        >
                            Load
                        </button>
                    </div>
                    {error && (
                        <div className="mt-4 p-3.5 rounded-xl bg-accent-rose/10 border border-accent-rose/20 text-accent-rose text-sm font-semibold animate-fade-in-up">
                            {error}
                        </div>
                    )}
                    {ambiguousMatches.length > 0 && (
                        <div className="mt-6 w-full text-left animate-fade-in-up">
                            <p className="text-xs font-semibold uppercase tracking-wider text-surface-400 mb-2.5">
                                Multiple projects match "{projectInput || activeProject}". Please select one:
                            </p>
                            <div className="flex flex-col gap-2 max-h-56 overflow-y-auto pr-1">
                                {ambiguousMatches.map((name) => (
                                    <button
                                        key={name}
                                        type="button"
                                        onClick={() => handleManualLoad(name)}
                                        className="flex items-center justify-between w-full px-4 py-3 text-sm font-medium text-surface-700 dark:text-surface-200 bg-surface-100/70 hover:bg-primary-50 dark:bg-surface-800/60 dark:hover:bg-primary-950/40 border border-surface-200/80 dark:border-surface-700/60 hover:border-primary-500/40 rounded-xl transition-all text-left group"
                                    >
                                        <span className="truncate group-hover:text-primary-600 dark:group-hover:text-primary-400 transition-colors">
                                            {name}
                                        </span>
                                        <span className="text-xs text-surface-400 group-hover:text-primary-500 transition-colors font-semibold ml-2 shrink-0">
                                            Select →
                                        </span>
                                    </button>
                                ))}
                            </div>
                        </div>
                    )}
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

    const priorityColor = (p) => {
        const pl = String(p).toLowerCase()
        if (pl === 'high' || pl === 'critical') return 'red'
        if (pl === 'medium') return 'orange'
        return 'green'
    }

    const totalTasks = data.tasks?.length || 0
    const totalEmployees = data.schedule ? [...new Set(data.schedule.map(s => s.assigned_empl))].length : 0
    const totalNotifications = data.telegram_notifications?.length || 0

    return (
        <div className="p-5 lg:p-8 max-w-[1400px] mx-auto space-y-6">
            {/* ── Header ──────── */}
            <div className="flex flex-col sm:flex-row sm:items-end sm:justify-between gap-4 animate-fade-in-up">
                <div>
                    <p className="text-[10px] font-bold text-primary-500 uppercase tracking-[0.2em] mb-1">Project Dashboard</p>
                    <h1 className="text-3xl font-black tracking-tight gradient-text">{data.project_name}</h1>
                </div>
                {data.prd && (
                    <div className="flex gap-2">
                        <a href={getDownloadUrl(`${data.project_name}_PRD.json`)} className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl glass-card text-sm font-bold text-primary-600 dark:text-primary-400 hover:shadow-md transition-all">
                            <FileJson className="w-4 h-4" /> JSON
                        </a>
                        <a href={getDownloadUrl(`${data.project_name}_PRD.pdf`)} className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-gradient-to-r from-primary-600 to-accent-violet text-white text-sm font-bold shadow-lg shadow-primary-500/20 hover:shadow-primary-500/35 transition-all">
                            <Download className="w-4 h-4" /> Download PDF
                        </a>
                    </div>
                )}
            </div>

            {/* ── Stat cards ──────── */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <StatCard icon={ListChecks} label="Total Tasks" value={totalTasks} gradient="from-primary-500 to-primary-600" glow="glow-indigo" delay={50} />
                <StatCard icon={UserCheck} label="Team Members" value={totalEmployees} gradient="from-accent-violet to-purple-600" glow="glow-violet" delay={100} />
                <StatCard icon={Bell} label="Notified" value={totalNotifications} gradient="from-accent-cyan to-blue-500" glow="glow-cyan" delay={150} />
                <StatCard icon={Calendar} label="Awaiting RSVP" value={data.meeting_summary?.Awaiting ?? 0} gradient="from-accent-amber to-orange-500" glow="glow-rose" delay={200} />
            </div>

            {/* ── PRD ──────── */}
            {data.prd && (
                <Section icon={FileText} title="Product Requirements" delay={250} badge={<Badge color="indigo">PRD</Badge>}>
                    <p className="text-sm text-surface-600 dark:text-surface-400 mb-5 leading-relaxed">
                        {data.prd.problem_statement}
                    </p>
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <div className="rounded-2xl bg-surface-50/80 dark:bg-surface-800/30 p-5 border border-surface-200/40 dark:border-surface-700/30">
                            <h4 className="text-[10px] font-bold text-accent-emerald uppercase tracking-widest mb-3 flex items-center gap-1.5">
                                <TrendingUp className="w-3 h-3" /> Goals
                            </h4>
                            <ul className="space-y-2">
                                {data.prd.goals?.map((g, i) => (
                                    <li key={i} className="text-sm text-surface-600 dark:text-surface-300 flex items-start gap-2.5 leading-relaxed">
                                        <ArrowUpRight className="w-3.5 h-3.5 mt-1 text-accent-emerald shrink-0" />
                                        {g}
                                    </li>
                                ))}
                            </ul>
                        </div>
                        <div className="rounded-2xl bg-surface-50/80 dark:bg-surface-800/30 p-5 border border-surface-200/40 dark:border-surface-700/30">
                            <h4 className="text-[10px] font-bold text-accent-amber uppercase tracking-widest mb-3">⚠ Constraints</h4>
                            <ul className="space-y-2">
                                {data.prd.constraints?.map((c, i) => (
                                    <li key={i} className="text-sm text-surface-600 dark:text-surface-300 flex items-start gap-2.5 leading-relaxed">
                                        <span className="text-accent-amber shrink-0 mt-0.5">▸</span>
                                        {c}
                                    </li>
                                ))}
                            </ul>
                        </div>
                    </div>
                </Section>
            )}

            {/* ── Tasks ──────── */}
            {data.tasks && (
                <Section icon={ListChecks} title="Generated Tasks" delay={300} badge={<Badge color="violet">{data.tasks.length} tasks</Badge>}>
                    <div className="overflow-x-auto rounded-2xl border border-surface-200/40 dark:border-surface-700/30">
                        <table className="premium-table">
                            <thead>
                                <tr className="bg-surface-50/80 dark:bg-surface-800/40 text-surface-400">
                                    <th className="text-left rounded-tl-2xl">ID</th>
                                    <th className="text-left">Task Name</th>
                                    <th className="text-left">Role</th>
                                    <th className="text-left">Priority</th>
                                    <th className="text-right">Days</th>
                                    <th className="text-left rounded-tr-2xl">Dependencies</th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.tasks.map((t, i) => (
                                    <tr key={i}>
                                        <td className="font-mono text-xs text-surface-400 font-bold">{t.task_id}</td>
                                        <td className="font-semibold max-w-[280px]">{t.task_name}</td>
                                        <td className="text-surface-500 text-xs font-medium">{t.assigned_role}</td>
                                        <td><Badge color={priorityColor(t.priority)}>{t.priority}</Badge></td>
                                        <td className="text-right font-mono font-bold text-sm">{t.time_days}</td>
                                        <td className="text-surface-400 text-xs font-medium">{Array.isArray(t.dependencies) ? t.dependencies.join(', ') || '—' : t.dependencies || '—'}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </Section>
            )}

            {/* ── Schedule ──────── */}
            {data.schedule && (
                <Section icon={CalendarDays} title="Project Schedule" delay={350} badge={<Badge color="blue">{data.schedule.length} entries</Badge>}>
                    <div className="overflow-x-auto rounded-2xl border border-surface-200/40 dark:border-surface-700/30">
                        <table className="premium-table">
                            <thead>
                                <tr className="bg-surface-50/80 dark:bg-surface-800/40 text-surface-400">
                                    <th className="text-left rounded-tl-2xl">Task</th>
                                    <th className="text-left">Assigned To</th>
                                    <th className="text-left">Email</th>
                                    <th className="text-left">Start</th>
                                    <th className="text-left">End</th>
                                    <th className="text-left rounded-tr-2xl">RACI</th>
                                </tr>
                            </thead>
                            <tbody>
                                {data.schedule.map((s, i) => (
                                    <tr key={i}>
                                        <td className="font-semibold max-w-[260px]">{s.task_name || s.task}</td>
                                        <td>
                                            <div className="flex items-center gap-2">
                                                <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-primary-500 to-accent-violet flex items-center justify-center text-white text-[10px] font-black shadow-sm">
                                                    {String(s.assigned_empl || '?').charAt(0).toUpperCase()}
                                                </div>
                                                <span className="text-sm font-medium">{s.assigned_empl}</span>
                                            </div>
                                        </td>
                                        <td className="text-surface-400 text-xs">{s.assigned_email || '—'}</td>
                                        <td className="font-mono text-xs font-semibold text-accent-emerald">{s.start_date ? String(s.start_date).slice(0, 10) : '—'}</td>
                                        <td className="font-mono text-xs font-semibold text-accent-rose">{s.end_date ? String(s.end_date).slice(0, 10) : '—'}</td>
                                        <td><Badge color="indigo">{s.RACI || '—'}</Badge></td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </Section>
            )}

            {/* ── Resource Assignments ──────── */}
            {data.assigned && (
                <Section icon={Users} title="Resource Assignments" delay={400}>
                    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
                        {Object.entries(
                            data.assigned.reduce((acc, t) => {
                                const name = t.Assigned_Employee || t.assigned_empl || 'Unassigned'
                                if (!acc[name]) acc[name] = { email: t.assigned_email || t.Email || '', tasks: [] }
                                acc[name].tasks.push(t.task_name || t.task || 'Task')
                                return acc
                            }, {})
                        ).map(([name, info]) => (
                            <div key={name} className="rounded-2xl bg-surface-50/60 dark:bg-surface-800/25 p-4 border border-surface-200/30 dark:border-surface-700/20 hover:border-primary-500/20 transition-all group">
                                <div className="flex items-center gap-3 mb-3">
                                    <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary-500 to-accent-violet flex items-center justify-center text-white text-sm font-black shadow-lg shadow-primary-500/15 group-hover:shadow-primary-500/30 transition-shadow">
                                        {name.charAt(0).toUpperCase()}
                                    </div>
                                    <div>
                                        <p className="text-sm font-bold">{name}</p>
                                        <p className="text-[10px] text-surface-400 font-medium">{info.email || 'No email'}</p>
                                    </div>
                                    <Badge color="violet">{info.tasks.length}</Badge>
                                </div>
                                <ul className="space-y-1.5">
                                    {info.tasks.map((t, j) => (
                                        <li key={j} className="text-xs text-surface-500 dark:text-surface-400 flex items-start gap-2 leading-relaxed">
                                            <span className="text-primary-400 mt-0.5 shrink-0">▸</span> {t}
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        ))}
                    </div>
                </Section>
            )}

            {/* ── Telegram Notifications ──────── */}
            {data.telegram_notifications && (
                <Section icon={Bell} title="Telegram Notifications" delay={450} badge={<Badge color="green">{data.telegram_notifications.length} sent</Badge>}>
                    <div className="flex flex-wrap gap-2">
                        {data.telegram_notifications.map((name, i) => (
                            <div
                                key={i}
                                className="inline-flex items-center gap-2 px-3.5 py-2 rounded-xl bg-accent-emerald/5 border border-accent-emerald/10 hover:bg-accent-emerald/10 transition-colors"
                            >
                                <CheckCircle2 className="w-3.5 h-3.5 text-accent-emerald" />
                                <span className="text-sm font-semibold text-accent-emerald">{name}</span>
                            </div>
                        ))}
                    </div>
                </Section>
            )}

            {/* ── Meeting Status ──────── */}
            {data.meeting_summary && (
                <Section icon={Video} title="Meeting Status" delay={500}>
                    <div className="flex items-center justify-between mb-5">
                        <div className="flex items-center gap-2">
                            <Badge color="indigo">{data.meeting_summary.meeting_type || 'Kickoff'}</Badge>
                            <Badge color={data.meeting_summary.status === 'Scheduled' ? 'green' : 'orange'}>
                                {data.meeting_summary.status}
                            </Badge>
                        </div>
                        <button
                            onClick={handleSync}
                            disabled={syncing}
                            className={`inline-flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs font-bold transition-all ${syncing
                                ? 'bg-surface-800 text-surface-500 cursor-not-allowed'
                                : 'bg-primary-500/10 text-primary-500 hover:bg-primary-500/20 border border-primary-500/15'
                                }`}
                        >
                            <RefreshCw className={`w-3.5 h-3.5 ${syncing ? 'animate-spin' : ''}`} />
                            {syncing ? 'Syncing...' : 'Sync Status'}
                        </button>
                    </div>
                    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
                        {[
                            { label: 'Accepted', value: data.meeting_summary.Accepted, icon: CheckCircle2, gradient: 'from-accent-emerald to-teal-500', glow: 'glow-emerald' },
                            { label: 'Declined', value: data.meeting_summary.Declined, icon: XCircle, gradient: 'from-accent-rose to-red-500', glow: 'glow-rose' },
                            { label: 'Tentative', value: data.meeting_summary.Tentative, icon: HelpCircle, gradient: 'from-accent-amber to-orange-500', glow: '' },
                            { label: 'Awaiting', value: data.meeting_summary.Awaiting, icon: Clock, gradient: 'from-accent-cyan to-blue-500', glow: 'glow-cyan' },
                        ].map(({ label, value, icon: StatIcon, gradient, glow }) => (
                            <div key={label} className={`stat-card glass-card text-center ${glow}`}>
                                <div className={`inline-flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br ${gradient} shadow-lg mb-2`}>
                                    <StatIcon className="w-5 h-5 text-white" />
                                </div>
                                <p className="text-3xl font-black tracking-tight">{value ?? 0}</p>
                                <p className="text-[10px] font-bold text-surface-400 mt-1 uppercase tracking-wider">{label}</p>
                            </div>
                        ))}
                    </div>
                </Section>
            )}

            {/* ── Bottom spacer ──────── */}
            <div className="h-6" />
        </div>
    )
}
