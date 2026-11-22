import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
    Rocket,
    FileText,
    ListChecks,
    Users,
    CheckCircle2,
    CalendarDays,
    Bell,
    ArrowDown,
    Loader2,
    UploadCloud,
    X,
    FolderUp,
    Calendar,
    Mail,
    Send,
    LogOut,
    Layout,
    Sparkles,
    Zap,
} from 'lucide-react'
import { startProject, uploadFile } from '../services/api'

const backends = [
    { value: 'excel', label: 'Excel' },
    { value: 'sql', label: 'SQL' },
    { value: 'odoo', label: 'Odoo ERP' },
    { value: 'csv', label: 'CSV' },
]

const integrations = [
    { key: 'google_calendar', label: 'Google Calendar (Meetings)', icon: Calendar, defaultOn: true },
    { key: 'gmail', label: 'Gmail (Notifications)', icon: Mail, defaultOn: true },
    { key: 'telegram', label: 'Telegram Bot (Employee RSVP)', icon: Send, defaultOn: true },
    { key: 'gantt', label: 'Gantt Chart Generation', icon: Layout, defaultOn: true },
]

const pipelineSteps = [
    { icon: FileText, label: 'Product Manager Agent', desc: 'Formulates project scope and key milestones based on PRD.', gradient: 'from-surface-800 to-surface-900', iconColor: 'text-accent-cyan' },
    { icon: ListChecks, label: 'Task Agent', desc: 'Decomposes PRD into structured development tasks.', gradient: 'from-surface-800 to-surface-900', iconColor: 'text-accent-cyan' },
    { icon: Users, label: 'Resource Agent', desc: 'Optimizes team allocation based on roles, skills, and availability.', gradient: 'from-surface-800 to-surface-900', iconColor: 'text-accent-cyan' },
    { icon: CheckCircle2, label: 'Validation Agent', desc: 'Verifies all task assignments and checks skill compatibility.', gradient: 'from-surface-800 to-surface-900', iconColor: 'text-accent-cyan' },
    { icon: CalendarDays, label: 'Scheduler Agent', desc: 'Generates dynamic Gantt charts and project milestones.', gradient: 'from-surface-800 to-surface-900', iconColor: 'text-accent-cyan' },
    { icon: Bell, label: 'Communication Agent', desc: 'Sends detailed invites, tracks RSVPs, and handles schedules.', gradient: 'from-surface-800 to-surface-900', iconColor: 'text-accent-cyan' },
]

export default function SetupPage({ setProjectCtx }) {
    const navigate = useNavigate()
    const [form, setForm] = useState({
        data_backend: 'excel',
        project_name: '',
        project_description: '',
    })
    const [checks, setChecks] = useState(
        Object.fromEntries(integrations.map((i) => [i.key, i.defaultOn]))
    )
    const [selectedFile, setSelectedFile] = useState(null)
    const [loading, setLoading] = useState(false)
    const [error, setError] = useState('')

    async function handleSubmit(e) {
        e.preventDefault()
        if (!form.project_name.trim() || !form.project_description.trim()) {
            setError('Please fill in all fields.')
            return
        }
        setError('')
        setLoading(true)
        try {
            if (selectedFile) {
                await uploadFile(selectedFile)
            }
            const res = await startProject({ ...form, integrations: checks })
            setProjectCtx({ jobId: res.job_id, projectName: form.project_name })
            navigate('/processing')
        } catch (err) {
            setError(err.response?.data?.error || 'Failed to start project. Is the API server running?')
            setLoading(false)
        }
    }

    return (
        <div className="min-h-screen p-6 lg:p-10 flex justify-center relative overflow-hidden bg-surface-950">
            {/* Background effects */}
            <div className="fixed inset-0 -z-10">
                <div className="absolute top-0 right-1/4 w-[600px] h-[600px] rounded-full bg-primary-500/[0.03] blur-[120px]" />
                <div className="absolute bottom-0 left-1/4 w-[500px] h-[500px] rounded-full bg-accent-cyan/[0.03] blur-[100px]" />
            </div>
            <div className="absolute inset-0 z-0 opacity-[0.02]"
                style={{
                    backgroundImage: `linear-gradient(rgba(255,255,255,0.5) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,0.5) 1px, transparent 1px)`,
                    backgroundSize: '48px 48px'
                }}
            />

            <div className="w-full max-w-7xl relative z-10">
                {/* ── Header ──────── */}
                <div className="mb-12 pt-2 animate-fade-in-up">
                    <div className="flex items-center gap-3 mb-4">
                        <div className="flex items-center justify-center w-10 h-10 rounded-xl bg-gradient-to-br from-primary-500/15 to-accent-violet/15">
                            <Sparkles className="w-5 h-5 text-primary-500" />
                        </div>
                        <span className="text-[10px] font-black text-primary-500 uppercase tracking-[0.3em]">New Project</span>
                    </div>
                    <h1 className="text-4xl lg:text-5xl font-black tracking-tight leading-[1.1] text-white">
                        Launch <span className="gradient-text">New Project</span>
                    </h1>
                    <p className="mt-4 text-surface-400 text-base font-medium max-w-2xl leading-relaxed">
                        Describe your idea — provide the context and goals, and our specialized AI agents
                        will architect the complete project framework for you.
                    </p>
                </div>

                {/* ── Two-column layout ──────── */}
                <div className="flex gap-8 lg:gap-12 items-start">
                    {/* ── Left: Form ──────── */}
                    <form
                        onSubmit={handleSubmit}
                        className="flex-1 w-full max-w-2xl flex flex-col gap-12"
                    >
                        {/* Project Name */}
                        <div className="animate-fade-in-up group" style={{ animationDelay: '100ms', animationFillMode: 'both' }}>
                            <label className="block text-[10px] font-black mb-2 ml-1 text-surface-500 uppercase tracking-[0.2em] group-focus-within:text-accent-cyan transition-colors">
                                Project Name <span className="text-accent-rose">*</span>
                            </label>
                            <div className="glass-card rounded-2xl p-5">
                                <input
                                    id="project-name-input"
                                    type="text"
                                    placeholder="e.g. Alpha Marketing Campaign 2026"
                                    value={form.project_name}
                                    onChange={(e) => setForm({ ...form, project_name: e.target.value })}
                                    className="w-full rounded-xl border border-surface-700/50 bg-surface-900/60 px-5 py-3.5 text-sm font-semibold text-surface-100 placeholder:text-surface-600 focus:outline-none focus:ring-2 focus:ring-accent-cyan/20 focus:border-accent-cyan/40 transition-all duration-300"
                                />
                            </div>
                        </div>

                        {/* Project Description */}
                        <div className="animate-fade-in-up group" style={{ animationDelay: '200ms', animationFillMode: 'both' }}>
                            <label className="block text-[10px] font-black mb-2 ml-1 text-surface-500 uppercase tracking-[0.2em] group-focus-within:text-accent-cyan transition-colors">
                                Project Description
                            </label>
                            <div className="glass-card rounded-2xl p-5">
                                <textarea
                                    id="project-description-input"
                                    rows={4}
                                    placeholder={`Provide: Design overview and key deliverables. Objectives and goals, e.g., "Design and launch a multi-platform loyalty program to increase user retention by 20%."`}
                                    value={form.project_description}
                                    onChange={(e) => setForm({ ...form, project_description: e.target.value })}
                                    className="w-full rounded-xl border border-surface-700/50 bg-surface-900/60 px-5 py-3.5 text-sm font-semibold text-surface-100 placeholder:text-surface-600 focus:outline-none focus:ring-2 focus:ring-accent-cyan/20 focus:border-accent-cyan/40 resize-none leading-relaxed transition-all duration-300"
                                />
                            </div>
                        </div>

                        {/* Data Source & Storage */}
                        <div className="animate-fade-in-up" style={{ animationDelay: '300ms', animationFillMode: 'both' }}>
                            <label className="block text-[10px] font-black mb-2 ml-1 text-surface-500 uppercase tracking-[0.2em]">
                                Data Ingestion Node
                            </label>
                            <div className="glass-card rounded-2xl p-5">

                                <div className="flex gap-2 mb-5">
                                    {backends.map((b) => (
                                        <button
                                            key={b.value}
                                            type="button"
                                            onClick={() => setForm({ ...form, data_backend: b.value })}
                                            className={`px-4 py-2 rounded-xl text-[11px] font-bold uppercase tracking-wider transition-all duration-200 border ${form.data_backend === b.value
                                                ? 'text-accent-cyan bg-accent-cyan/10 border-accent-cyan/20 shadow-sm shadow-accent-cyan/5'
                                                : 'text-surface-500 hover:text-surface-300 border-surface-800/50 hover:border-surface-700/50 bg-surface-900/40'
                                                }`}
                                        >
                                            {b.label}
                                        </button>
                                    ))}
                                </div>

                                {/* File Upload Zone */}
                                <div
                                    className={`relative group rounded-2xl border-2 border-dashed transition-all duration-300 ${selectedFile
                                        ? 'border-accent-emerald/30 bg-accent-emerald/5'
                                        : 'border-surface-700/30 hover:border-accent-cyan/20 bg-surface-900/30 hover:bg-surface-900/50'
                                        }`}
                                >
                                    {!selectedFile ? (
                                        <label className="flex flex-col items-center justify-center w-full py-10 cursor-pointer">
                                            <div className="mb-3 p-3 rounded-2xl bg-surface-800/40 text-surface-500 group-hover:text-accent-cyan group-hover:bg-accent-cyan/5 transition-all duration-300">
                                                <UploadCloud className="w-8 h-8" />
                                            </div>
                                            <p className="text-sm font-bold text-surface-200">
                                                Click to upload or drag & drop
                                            </p>
                                            <p className="text-[10px] text-surface-600 font-bold uppercase tracking-wider mt-1.5">
                                                Excel, CSV, or JSON (max 10MB)
                                            </p>
                                            <input
                                                type="file"
                                                className="hidden"
                                                accept=".xlsx,.xls,.csv,.json"
                                                onChange={(e) => {
                                                    if (e.target.files && e.target.files[0]) {
                                                        setSelectedFile(e.target.files[0])
                                                    }
                                                }}
                                            />
                                        </label>
                                    ) : (
                                        <div className="flex items-center justify-between px-6 py-5">
                                            <div className="flex items-center gap-4">
                                                <div className="w-11 h-11 rounded-xl bg-accent-emerald/10 flex items-center justify-center text-accent-emerald border border-accent-emerald/20">
                                                    <FolderUp className="w-5 h-5" />
                                                </div>
                                                <div>
                                                    <p className="text-sm font-bold text-surface-100">
                                                        {selectedFile.name}
                                                    </p>
                                                    <p className="text-[10px] text-accent-emerald font-black tracking-widest uppercase mt-0.5">
                                                        {(selectedFile.size / 1024).toFixed(1)} KB • Ready
                                                    </p>
                                                </div>
                                            </div>
                                            <button
                                                type="button"
                                                onClick={() => setSelectedFile(null)}
                                                className="p-2 rounded-lg text-surface-600 hover:text-accent-rose hover:bg-accent-rose/5 transition-all"
                                            >
                                                <X className="w-5 h-5" />
                                            </button>
                                        </div>
                                    )}
                                </div>
                            </div>
                        </div>

                        {/* Enabled Modules & Integrations */}
                        <div className="animate-fade-in-up" style={{ animationDelay: '400ms', animationFillMode: 'both' }}>
                            <label className="block text-[10px] font-black mb-2 ml-1 text-surface-500 uppercase tracking-[0.2em]">
                                Enabled Modules & Integrations
                            </label>
                            <div className="glass-card rounded-2xl p-5">
                                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                                    {integrations.map((int) => {
                                        const Icon = int.icon;
                                        return (
                                            <label
                                                key={int.key}
                                                className={`flex items-center gap-3.5 cursor-pointer group select-none border p-4 rounded-xl transition-all duration-300 ${checks[int.key]
                                                    ? 'border-accent-cyan/15 bg-accent-cyan/[0.03] shadow-sm'
                                                    : 'border-surface-800/40 opacity-50 hover:opacity-100 hover:border-surface-700/50 bg-surface-900/20'
                                                    }`}
                                            >
                                                <div
                                                    className={`flex items-center justify-center w-5 h-5 rounded-md border-2 transition-all duration-300 shrink-0 ${checks[int.key]
                                                        ? 'bg-accent-cyan border-accent-cyan'
                                                        : 'border-surface-700 bg-surface-900 group-hover:border-surface-600'
                                                        }`}
                                                >
                                                    {checks[int.key] && (
                                                        <svg className="w-3.5 h-3.5 text-surface-950" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={5}>
                                                            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                                                        </svg>
                                                    )}
                                                </div>
                                                <input
                                                    type="checkbox"
                                                    className="sr-only"
                                                    checked={checks[int.key]}
                                                    onChange={(e) => setChecks({ ...checks, [int.key]: e.target.checked })}
                                                />
                                                <div className="flex items-center gap-2.5 min-w-0">
                                                    {Icon && (
                                                        <div className={`p-1.5 rounded-lg shrink-0 ${int.key === 'google_calendar' ? 'text-[#F4B400]' :
                                                            int.key === 'gmail' ? 'text-[#DB4437]' :
                                                                int.key === 'telegram' ? 'text-[#0088CC]' :
                                                                    'text-[#00A3BF]'
                                                            }`}>
                                                            <Icon className="w-4 h-4" />
                                                        </div>
                                                    )}
                                                    <span className={`text-[12px] font-bold transition-colors truncate ${checks[int.key] ? 'text-surface-100' : 'text-surface-500'}`}>
                                                        {int.label}
                                                    </span>
                                                </div>
                                            </label>
                                        )
                                    })}
                                </div>
                            </div>
                        </div>

                        {/* Error */}
                        {error && (
                            <div className="px-5 py-4 rounded-xl bg-accent-rose/5 border border-accent-rose/15 text-accent-rose text-sm font-semibold animate-fade-in-up flex items-center gap-3">
                                <X className="w-4 h-4 shrink-0" />
                                {error}
                            </div>
                        )}

                        {/* Submit */}
                        <div className="animate-fade-in-up pt-2" style={{ animationDelay: '500ms', animationFillMode: 'both' }}>
                            <button
                                id="generate-project-btn"
                                type="submit"
                                disabled={loading}
                                className="relative group w-full flex items-center justify-center gap-3 px-8 py-4.5 rounded-2xl bg-gradient-to-r from-primary-600 via-accent-violet to-primary-500 hover:from-primary-500 hover:via-accent-violet hover:to-primary-400 text-white font-black uppercase tracking-[0.15em] text-[12px] shadow-2xl shadow-primary-500/20 hover:shadow-primary-500/35 transition-all duration-300 active:scale-[0.98] overflow-hidden border border-white/10 animate-gradient"
                            >
                                <div className="absolute inset-0 bg-gradient-to-r from-white/0 via-white/5 to-white/0 translate-x-[-100%] group-hover:translate-x-[100%] transition-transform duration-700" />
                                {loading ? (
                                    <span className="flex items-center gap-3 relative z-10">
                                        <Loader2 className="w-5 h-5 animate-spin" />
                                        INITIALIZING AGENTS...
                                    </span>
                                ) : (
                                    <span className="flex items-center gap-3 relative z-10">
                                        <Zap className="w-5 h-5" />
                                        LAUNCH AI PROJECT PIPELINE
                                    </span>
                                )}
                            </button>
                        </div>
                    </form>

                    {/* ── Right: Agent Pipeline Preview ──────── */}
                    <div
                        className="w-[400px] shrink-0 glass-card rounded-3xl p-7 animate-fade-in-up sticky top-8 hidden xl:block"
                        style={{ animationDelay: '150ms' }}
                    >
                        <div className="mb-8">
                            <p className="text-[10px] font-black text-surface-600 uppercase tracking-[0.3em] mb-2">
                                Pipeline Architecture
                            </p>
                            <h3 className="text-lg font-black text-white leading-tight">
                                Agent Execution Flow
                            </h3>
                        </div>
                        <div className="space-y-6">
                            {pipelineSteps.map((step, i) => {
                                const Icon = step.icon
                                return (
                                    <div key={step.label} className="relative">
                                        <div className="flex items-center gap-5 py-5 px-6 rounded-2xl bg-surface-900/40 border border-surface-800/40 hover:border-accent-cyan/20 transition-all duration-300 group">
                                            <div className={`flex items-center justify-center w-11 h-11 rounded-xl bg-gradient-to-br ${step.gradient} border border-surface-700/50 shadow-lg shrink-0 group-hover:border-accent-cyan/30 transition-all`}>
                                                <Icon className={`w-5 h-5 ${step.iconColor}`} />
                                            </div>
                                            <div className="min-w-0 flex-1">
                                                <p className="text-[14px] font-black text-surface-100 group-hover:text-accent-cyan transition-colors leading-tight">
                                                    {step.label}
                                                </p>
                                                <p className="text-[11px] text-surface-500 font-medium leading-relaxed mt-1.5">
                                                    {step.desc}
                                                </p>
                                            </div>
                                        </div>
                                        {i < pipelineSteps.length - 1 && (
                                            <div className="flex justify-center py-3">
                                                <div className="w-px h-10 bg-gradient-to-b from-accent-cyan/30 via-accent-cyan/10 to-transparent" />
                                            </div>
                                        )}
                                    </div>
                                )
                            })}
                        </div>
                    </div>
                </div>
            </div >
        </div >
    )
}
