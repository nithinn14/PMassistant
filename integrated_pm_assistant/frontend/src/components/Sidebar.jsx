import { NavLink, useLocation } from 'react-router-dom'
import {
    LayoutDashboard,
    FolderPlus,
    FolderKanban,
    Loader2,
    Sun,
    Moon,
    Sparkles,
    ChevronRight,
    ListChecks,
    Users,
    CalendarDays,
    BarChart3,
    Bell,
    Activity,
} from 'lucide-react'

const sections = [
    {
        label: 'WORKSPACE',
        links: [
            { to: '/dashboard', icon: LayoutDashboard, label: 'Dashboard' },
            { to: '/', icon: FolderPlus, label: 'New Project', badge: 'AI' },
            { to: '/projects', icon: FolderKanban, label: 'Projects' },
        ],
    },
    {
        label: 'AGENTS',
        links: [
            { to: '/processing', icon: Activity, label: 'Agent Status', dot: true },
            { to: '/tasks', icon: ListChecks, label: 'Task Board' },
            { to: '/resources', icon: Users, label: 'Resources' },
        ],
    },
    {
        label: 'COMMUNICATION',
        links: [
            { to: '/meetings', icon: CalendarDays, label: 'Meetings' },
            { to: '/gantt', icon: BarChart3, label: 'Gantt Chart' },
            { to: '/notifications', icon: Bell, label: 'Notifications' },
        ],
    },
]

const PROJECT_ROUTES = ['/dashboard', '/tasks', '/resources', '/meetings', '/gantt', '/notifications']

export default function Sidebar({ dark, setDark }) {
    const location = useLocation()
    const searchParams = new URLSearchParams(location.search)
    const currentProject = searchParams.get('project')

    return (
        <aside className="hidden md:flex flex-col w-64 border-r border-surface-800/40 bg-surface-950/98 backdrop-blur-3xl transition-all duration-500 shrink-0">
            {/* ── Logo ──────────── */}
            <div className="px-6 pt-8 pb-6">
                <div className="flex items-center gap-3 group px-1">
                    <div className="flex items-center justify-center w-11 h-11 rounded-2xl bg-gradient-to-br from-accent-cyan to-accent-emerald shadow-xl shadow-accent-cyan/20 group-hover:scale-105 transition-transform duration-300">
                        <Sparkles className="w-6 h-6 text-white" />
                    </div>
                    <div>
                        <h1 className="text-base font-black tracking-tight text-white leading-none">PM ASSISTANT</h1>
                        <p className="text-[10px] text-surface-500 font-bold tracking-[0.2em] uppercase mt-1">
                            PM INTELLIGENCE
                        </p>
                    </div>
                </div>
            </div>

            {/* ── Navigation ────── */}
            <nav className="flex-1 px-4 py-2 space-y-7 overflow-y-auto scrollbar-none">
                {sections.map((section) => (
                    <div key={section.label} className="space-y-2">
                        <p className="px-4 text-[10px] font-black text-surface-600 uppercase tracking-[0.25em] opacity-80">
                            {section.label}
                        </p>
                        <div className="space-y-1">
                            {section.links.map(({ to, icon: Icon, label, badge, dot, count }) => {
                                const active = location.pathname === to
                                const targetTo = (currentProject && PROJECT_ROUTES.includes(to))
                                    ? `${to}?project=${encodeURIComponent(currentProject)}`
                                    : to
                                return (
                                    <NavLink
                                        key={to}
                                        to={targetTo}
                                        className={({ isActive }) => `
                                            group relative flex items-center gap-3 px-4 py-2.5 rounded-xl text-sm font-semibold transition-all duration-200
                                            ${isActive
                                                ? 'bg-accent-cyan/10 text-accent-cyan shadow-[inset_0_0_20px_rgba(34,211,238,0.05)]'
                                                : 'text-surface-400 hover:bg-surface-800/40 hover:text-surface-100'
                                            }
                                        `}
                                    >
                                        {active && (
                                            <div className="absolute left-0 top-2 bottom-2 w-1 rounded-r-full bg-accent-cyan shadow-[0_0_12px_rgba(34,211,238,0.6)]" />
                                        )}
                                        <Icon className={`w-4 h-4 shrink-0 transition-transform duration-200 group-hover:scale-110 ${active ? 'text-accent-cyan' : 'text-surface-500 group-hover:text-surface-300'}`} />
                                        <span className="flex-1 tracking-tight">{label}</span>
                                        {badge && (
                                            <span className="px-2 py-[2px] rounded-lg text-[9px] font-black bg-accent-cyan/15 text-accent-cyan border border-accent-cyan/10 uppercase tracking-tighter">
                                                {badge}
                                            </span>
                                        )}
                                        {dot && (
                                            <div className="relative flex h-2 w-2">
                                                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent-emerald opacity-75"></span>
                                                <span className="relative inline-flex rounded-full h-2 w-2 bg-accent-emerald"></span>
                                            </div>
                                        )}
                                        {count && (
                                            <span className="flex items-center justify-center min-w-[1.25rem] h-5 px-1.5 rounded-full bg-accent-rose text-white text-[10px] font-black shadow-lg shadow-accent-rose/20">
                                                {count}
                                            </span>
                                        )}
                                    </NavLink>
                                )
                            })}
                        </div>
                    </div>
                ))}
            </nav>

            {/* ── Bottom ────────── */}
            <div className="px-4 py-6 space-y-6 border-t border-surface-800/40 bg-surface-950/40">
                {/* Data Backend pills */}
                <div className="px-2">
                    <p className="mb-3 text-[10px] font-black text-surface-600 uppercase tracking-[0.2em]">
                        DATA BACKEND
                    </p>
                    <div className="grid grid-cols-2 gap-2">
                        {['Excel', 'SQL', 'Odoo', 'CSV'].map((b, i) => (
                            <span
                                key={b}
                                className={`flex items-center justify-center py-1.5 rounded-lg text-[10px] font-extrabold cursor-pointer transition-all border ${i === 0
                                    ? 'bg-accent-cyan/10 text-accent-cyan border-accent-cyan/20 shadow-sm shadow-accent-cyan/5'
                                    : 'text-surface-500 hover:text-surface-300 border-surface-800/50 hover:border-surface-700/50 bg-surface-900/40'
                                    }`}
                            >
                                {b}
                            </span>
                        ))}
                    </div>
                </div>

                <div className="space-y-3">
                    {/* Status */}
                    <div className="flex items-center gap-2.5 px-3 py-2 rounded-xl bg-accent-emerald/5 border border-accent-emerald/10">
                        <div className="relative flex h-2 w-2">
                            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-accent-emerald opacity-40"></span>
                            <span className="relative inline-flex rounded-full h-2 w-2 bg-accent-emerald"></span>
                        </div>
                        <span className="text-[10px] font-bold text-accent-emerald uppercase tracking-wider">Systems Live</span>
                    </div>

                    {/* Dark mode */}
                    <button
                        id="dark-mode-toggle"
                        onClick={() => setDark(!dark)}
                        className="flex items-center gap-3 w-full px-4 py-3 rounded-xl text-sm font-bold text-surface-400 hover:bg-surface-800/50 hover:text-surface-100 transition-all border border-transparent hover:border-surface-800/50 group"
                    >
                        <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-surface-900 group-hover:bg-surface-800 transition-colors">
                            {dark ? <Sun className="w-4 h-4 text-amber-400" /> : <Moon className="w-4 h-4" />}
                        </div>
                        <span className="flex-1 text-left">{dark ? 'Light Mode' : 'Dark Mode'}</span>
                    </button>
                </div>
            </div>
        </aside>
    )
}
