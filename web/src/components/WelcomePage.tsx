import { useState, useEffect, useRef, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { Send, Paperclip, Brain, Globe, Database, ChevronDown, ListTree, ChevronRight } from 'lucide-react'
import { useIsMobile } from '@/hooks/use-mobile'
import { FileCard } from '@/features/chat/components/FileCard'
import type { ModelOption, SessionConfig, PendingPermissionAsk, UploadedFileItem, WorkflowPlan } from '@/types/app'

const PINNED_MODELS = ['deepseek_v4_flash', 'deepseek_v4_pro', 'qwen_36_plus', 'qwen_35_plus', 'qwen3_6_27b_local', 'glm_51', 'glm_5', 'kimi_k2_5', 'kimi_k2_6', 'minimax_m25', 'minimax_m21']

function sortModels(models: ModelOption[]): ModelOption[] {
  const pinned: ModelOption[] = []
  const rest: ModelOption[] = []
  for (const m of models) {
    const idx = PINNED_MODELS.indexOf(m.key)
    if (idx >= 0) pinned[idx] = m
    else rest.push(m)
  }
  return [...pinned.filter(Boolean), ...rest]
}

const MODEL_ICON_MAP: Record<string, string> = {
  deepseek_v4_flash: 'deepseek', deepseek_v4_pro: 'deepseek',
  qwen_36_plus: 'qwen', qwen_35_plus: 'qwen', qwen3_6_27b_local: 'qwen',
  glm_51: 'zhipu', glm_5: 'zhipu',
  kimi_k2_5: 'kimi', kimi_k2_6: 'kimi',
  minimax_m25: 'minimax', minimax_m21: 'minimax',
}

const SVG_ICONS_WITH_WHITE_FILL = new Set(['kimi'])

function getModelIconUrl(modelKey: string): string | null {
  const id = MODEL_ICON_MAP[modelKey]
  if (!id) return null
  if (SVG_ICONS_WITH_WHITE_FILL.has(id))
    return `https://registry.npmmirror.com/@lobehub/icons-static-png/latest/files/light/${id}-color.png`
  return `https://registry.npmmirror.com/@lobehub/icons-static-svg/latest/files/icons/${id}-color.svg`
}

function ModelIcon({ modelKey, size = 14 }: { modelKey: string; size?: number }) {
  const url = getModelIconUrl(modelKey)
  if (!url) return null
  return <img src={url} alt="" width={size} height={size} className="flex-shrink-0" style={{ objectFit: 'contain' }} />
}

const PROVERBS = [
  { text: '大水无过望，小水不过庚', source: '防汛经验' },
  { text: '春雨贵如油，夏雨遍地流', source: '农谚' },
  { text: '小满江河满，芒种水满田', source: '节气谚语' },
  { text: '七下八上，防汛关键', source: '北方防汛口诀' },
  { text: '天有不测风云，水有无常涨落', source: '水文哲理' },
  { text: '水涨船高，风大浪急', source: '水文观察' },
  { text: '不怕初一阴，就怕初二下', source: '天气谚语' },
  { text: '天上钩钩云，地上雨淋淋', source: '气象谚语' },
  { text: '东虹日头西虹雨', source: '气象谚语' },
  { text: '八月十五云遮月，正月十五雪打灯', source: '长期预报谚语' },
  { text: '清早浮云走，午后晒死狗', source: '天气谚语' },
  { text: '有雨山戴帽，无雨山没腰', source: '天气谚语' },
]

interface WelcomePageProps {
  value: string
  disabled?: boolean
  models: ModelOption[]
  config: SessionConfig
  files?: UploadedFileItem[]
  workflow?: WorkflowPlan | null
  onChange: (value: string) => void
  onSubmit: () => void
  onUpload: (file: File) => void
  onConfigChange: (config: SessionConfig) => void
}

export default function WelcomePage({
  value,
  disabled,
  models,
  config,
  files = [],
  workflow,
  onChange,
  onSubmit,
  onUpload,
  onConfigChange,
}: WelcomePageProps) {
  const isMobile = useIsMobile();
  const [mounted, setMounted] = useState(false)
  const [proverb] = useState(() => PROVERBS[Math.floor(Math.random() * PROVERBS.length)])
  const [modelOpen, setModelOpen] = useState(false)
  const [menuPos, setMenuPos] = useState<{ top: number; left: number; minWidth: number } | null>(null)
  const [workflowExpanded, setWorkflowExpanded] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const modelBtnRef = useRef<HTMLButtonElement>(null)

  const currentModel = models.find((m) => m.key === config.model_key)
  const sortedModels = sortModels(models)

  const updateMenuPos = useCallback(() => {
    if (!modelBtnRef.current) return
    const rect = modelBtnRef.current.getBoundingClientRect()
    setMenuPos({ top: rect.top - 8, left: rect.left, minWidth: Math.max(rect.width, 220) })
  }, [])

  useEffect(() => {
    if (modelOpen) {
      updateMenuPos()
      const onScroll = () => updateMenuPos()
      const onResize = () => updateMenuPos()
      window.addEventListener('scroll', onScroll, true)
      window.addEventListener('resize', onResize)
      return () => {
        window.removeEventListener('scroll', onScroll, true)
        window.removeEventListener('resize', onResize)
      }
    } else {
      setMenuPos(null)
    }
  }, [modelOpen, updateMenuPos])

  useEffect(() => { setMounted(true) }, [])

  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 160) + 'px'
    }
  }, [value])

  function selectModel(model: ModelOption) {
    setModelOpen(false)
    const next: SessionConfig = { ...config, model_key: model.key }
    if (!model.supports_reasoning) next.enable_reasoning = false
    onConfigChange(next)
  }

  function toggleReasoning() {
    if (!currentModel?.supports_reasoning) return
    onConfigChange({ ...config, enable_reasoning: !config.enable_reasoning })
  }

  function toggleSearch() {
    onConfigChange({ ...config, enable_search: !config.enable_search })
  }

  const transitionClass = (delay: number) =>
    `transition-all duration-700 ease-out ${mounted ? 'opacity-100 translate-y-0' : 'opacity-0 translate-y-4'}`
  const delayStyle = (ms: number) => ({ transitionDelay: `${ms}ms` })

  const completedSteps = workflow?.steps?.filter((s) => s.status === "completed").length || 0;
  const totalSteps = workflow?.steps?.length || 0;
  const hasContext = files.length > 0 || (workflow?.steps?.length || 0) > 0;

  return (
    <div className="flex-1 flex flex-col items-center justify-center relative overflow-hidden hydro-wave-bg">
      {/* Animated atmospheric layers */}
      <div className="absolute inset-0 pointer-events-none overflow-hidden">
        {/* Primary glow */}
        <div className="absolute top-[5%] left-[15%] w-[600px] h-[600px] rounded-full opacity-[0.07] blur-[120px] animate-float"
          style={{ background: 'radial-gradient(circle, var(--ocean-400), transparent 70%)' }} />
        {/* Secondary glow */}
        <div className="absolute bottom-[5%] right-[10%] w-[500px] h-[500px] rounded-full opacity-[0.05] blur-[100px]"
          style={{ background: 'radial-gradient(circle, var(--teal-400), transparent 70%)', animation: 'float 5s ease-in-out infinite reverse' }} />
        {/* Accent glow */}
        <div className="absolute top-[40%] right-[25%] w-[300px] h-[300px] rounded-full opacity-[0.03] blur-[80px]"
          style={{ background: 'radial-gradient(circle, var(--amber-400), transparent 70%)', animation: 'float 6s ease-in-out infinite' }} />
        {/* Grid pattern */}
        <div className="absolute inset-0 opacity-[0.02]"
          style={{
            backgroundImage: `linear-gradient(var(--ocean-400) 1px, transparent 1px), linear-gradient(90deg, var(--ocean-400) 1px, transparent 1px)`,
            backgroundSize: '72px 72px',
          }} />
        {/* Flowing wave lines */}
        <svg className="absolute bottom-[20%] left-0 w-full h-[200px] opacity-[0.03]" viewBox="0 0 1200 200" preserveAspectRatio="none">
          <path d="M0 100 C300 20, 600 180, 900 80 S1100 140, 1200 100" fill="none" stroke="var(--ocean-400)" strokeWidth="1.5" className="animate-wave-flow" />
          <path d="M0 120 C250 60, 550 160, 850 100 S1050 160, 1200 120" fill="none" stroke="var(--teal-400)" strokeWidth="1" className="animate-wave-flow" style={{ animationDelay: '2s' }} />
        </svg>
      </div>

      <div className="relative z-10 w-full max-w-[660px] px-5 flex flex-col items-center">
        {/* Brand */}
        <div className={`flex flex-col items-center mb-6 ${transitionClass(0)}`} style={delayStyle(0)}>
          <div className="relative mb-5">
            <div className={`inline-flex items-center justify-center ${isMobile ? 'w-12 h-12' : 'w-14 h-14'} rounded-2xl animate-glow-pulse`}
              style={{ background: 'var(--ocean-500)', boxShadow: '0 8px 32px rgba(37,99,168,0.25)' }}>
              <img src="/floodmind-icon.svg" alt="FloodMind" className={`${isMobile ? 'w-6 h-6' : 'w-8 h-8'}`} style={{ filter: 'brightness(0) invert(1)' }} />
            </div>
            <div className="absolute -bottom-1 -right-1 w-4 h-4 rounded-full border-2 flex items-center justify-center" style={{ background: 'var(--teal-400)', borderColor: 'hsl(var(--background))' }}>
              <svg width="6" height="6" viewBox="0 0 24 24" fill="white"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z" /></svg>
            </div>
          </div>
          <h1 className={`${isMobile ? 'text-[22px]' : 'text-[28px]'} font-semibold tracking-tight mb-2`} style={{ color: 'hsl(var(--foreground))', fontFamily: 'var(--font-display)' }}>
            FloodMind
          </h1>
          <p className="text-[13px] text-center max-w-[360px] leading-relaxed" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.55 }}>
            智能水文预报助手 — 融合多源数据与 AI 推理，为流域洪水预报提供全链路决策支持
          </p>
        </div>

        {/* Proverb */}
        <div className={`w-full mb-8 ${transitionClass(80)}`} style={delayStyle(80)}>
          <div className="flex flex-col items-center text-center">
            <p className="text-[18px] font-medium tracking-wide leading-relaxed"
              style={{ color: 'hsl(var(--foreground))', fontFamily: 'var(--font-display)', opacity: 0.85 }}>
              「{proverb.text}」
            </p>
            <p className="text-[11px] mt-2 tracking-wider"
              style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.35 }}>
              —— {proverb.source}
            </p>
          </div>
        </div>

        {/* Inline context bar */}
        {hasContext && (
          <div className={`w-full mb-4 ${transitionClass(120)}`} style={delayStyle(120)}>
            <div
              className="rounded-xl overflow-hidden"
              style={{
                background: 'var(--glass-bg)',
                border: '1px solid hsl(var(--border))',
                backdropFilter: 'blur(8px)',
              }}
            >
              {/* File cards */}
              {files.length > 0 && (
                <div className="flex items-start gap-3 px-4 py-3 overflow-x-auto">
                  {files.map((file) => (
                    <FileCard
                      key={file.id}
                      file={file}
                    />
                  ))}
                </div>
              )}

              {/* Workflow steps */}
              {workflow?.steps && workflow.steps.length > 0 && (
                <div style={{ borderTop: files.length > 0 ? '1px solid hsl(var(--border))' : 'none' }}>
                  <button
                    onClick={() => setWorkflowExpanded(!workflowExpanded)}
                    className="w-full flex items-center gap-2 px-3 py-2 text-[11px] transition-colors duration-200"
                    onMouseEnter={(e) => { e.currentTarget.style.background = 'hsl(var(--muted))'; }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                  >
                    <ListTree size={12} style={{ color: 'var(--teal-400)' }} strokeWidth={1.8} />
                    <div className="h-1 flex-1 rounded-full overflow-hidden max-w-[80px]" style={{ background: 'hsl(var(--muted))' }}>
                      <div className="h-full rounded-full transition-all duration-500" style={{ width: `${(completedSteps / totalSteps) * 100}%`, background: 'var(--gradient-ocean-teal)' }} />
                    </div>
                    <span className="font-semibold" style={{ color: 'hsl(var(--foreground))' }}>
                      {completedSteps}/{totalSteps}
                    </span>
                    <span className="truncate max-w-[140px]" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.5 }}>
                      {workflow.steps.find(s => s.status === 'running')?.title || workflow.steps[workflow.steps.length - 1]?.title || ''}
                    </span>
                    <span className="ml-auto flex-shrink-0" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.3 }}>
                      {workflowExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
                    </span>
                  </button>
                  {workflowExpanded && (
                    <div className="px-3 pb-2 flex flex-col gap-0.5">
                      {workflow.steps.map((step, index) => (
                        <div key={step.key || `${index}`} className="flex items-center gap-2 py-1 px-2 rounded-md transition-colors duration-200"
                          onMouseEnter={(e) => { e.currentTarget.style.background = 'hsl(var(--muted))'; }}
                          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; }}
                        >
                          <div className="w-3 h-3 rounded-full flex items-center justify-center flex-shrink-0"
                            style={{
                              background: step.status === "completed" ? 'var(--teal-50)' : step.status === "running" ? 'var(--ocean-50)' : step.status === "error" ? '#fef2f2' : 'hsl(var(--muted))',
                              color: step.status === "completed" ? 'var(--teal-500)' : step.status === "running" ? 'var(--ocean-500)' : step.status === "error" ? 'hsl(var(--destructive))' : 'hsl(var(--muted-foreground))',
                            }}
                          >
                            {step.status === "running" ? (
                              <SparkleIcon size={7} />
                            ) : step.status === "completed" ? (
                              <svg width="7" height="7" viewBox="0 0 24 24" fill="currentColor"><path d="M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z" /></svg>
                            ) : step.status === "error" ? (
                              <svg width="7" height="7" viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="10" /></svg>
                            ) : (
                              <svg width="7" height="7" viewBox="0 0 24 24" fill="currentColor" opacity="0.3"><circle cx="12" cy="12" r="10" /></svg>
                            )}
                          </div>
                          <span className="text-[10px] truncate"
                            style={{
                              color: step.status === "completed" ? 'var(--teal-600)' : step.status === "error" ? 'hsl(var(--destructive))' : step.status === "running" ? 'hsl(var(--foreground))' : 'hsl(var(--muted-foreground))',
                              opacity: step.status === "pending" ? 0.45 : 1,
                            }}
                          >
                            {step.title || step.label}
                          </span>
                          {step.status === "running" && (
                            <span className="ml-auto text-[8px] font-semibold animate-pulse-subtle flex-shrink-0" style={{ color: 'var(--ocean-400)', opacity: 0.5 }}>
                              执行中
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Input card */}
        <div className={`w-full ${transitionClass(160)}`} style={delayStyle(160)}>
          <div className="rounded-2xl overflow-hidden"
            style={{
              background: 'var(--gradient-card)',
              border: '1px solid hsl(var(--border))',
              boxShadow: '0 8px 40px -8px rgba(15,31,56,0.1), 0 0 0 1px rgba(37,99,168,0.03)',
              backdropFilter: 'blur(12px)',
            }}
          >
            {/* Textarea row */}
            <div className="flex items-end">
              <button
                onClick={() => fileInputRef.current?.click()}
                className="flex-shrink-0 p-3.5 transition-all duration-200 rounded-tl-2xl"
                style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.3 }}
                onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--ocean-500)'; e.currentTarget.style.opacity = '1'; e.currentTarget.style.background = 'var(--ocean-50)' }}
                onMouseLeave={(e) => { e.currentTarget.style.color = 'hsl(var(--muted-foreground))'; e.currentTarget.style.opacity = '0.3'; e.currentTarget.style.background = 'transparent' }}
                title="上传文件"
              >
                <Paperclip size={17} strokeWidth={1.7} />
              </button>
              <input ref={fileInputRef} type="file"           accept=".csv,.xlsx,.xls,.txt,.json,.docx,.pdf,.md,.png,.jpg,.jpeg,.webp,.gif,.bmp" className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) {
                    const imageExts = ['.png','.jpg','.jpeg','.webp','.gif','.bmp'];
                    const ext = '.' + file.name.split('.').pop()?.toLowerCase();
                    if (imageExts.includes(ext) && currentModel && !currentModel.supports_vision) {
                      alert(`当前模型 ${currentModel.label} 不支持图像理解，请切换至支持视觉的模型后再上传图片。`);
                      e.target.value = '';
                      return;
                    }
                    onUpload(file);
                  }
                  e.target.value = '';
                }} />

              <textarea
                ref={textareaRef}
                value={value}
                onChange={(e) => onChange(e.target.value)}
                onKeyDown={(e) => {
                  if (disabled) return
                  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); if (value.trim()) onSubmit() }
                }}
                placeholder="描述你的预报任务..."
                rows={1}
                className="flex-1 max-h-[160px] min-h-[56px] py-4 px-2 bg-transparent resize-none outline-none text-[15px] leading-relaxed placeholder:opacity-25"
                style={{ color: 'hsl(var(--foreground))', fontFamily: 'var(--font-body)' }}
                disabled={disabled}
              />

              <button
                onClick={() => { if (value.trim()) onSubmit() }}
                disabled={disabled || !value.trim()}
                className="flex-shrink-0 m-2 w-9 h-9 rounded-xl flex items-center justify-center transition-all duration-300 active:scale-90"
                style={{
                  background: value.trim() && !disabled
                    ? 'var(--gradient-ocean-teal)'
                    : 'hsl(var(--muted))',
                  color: value.trim() && !disabled ? 'white' : 'hsl(var(--muted-foreground))',
                  boxShadow: value.trim() && !disabled ? '0 3px 12px rgba(37,99,168,0.25)' : 'none',
                  opacity: value.trim() && !disabled ? 1 : 0.25,
                }}
              >
                <Send size={15} strokeWidth={2} className={value.trim() ? 'ml-0.5' : ''} />
              </button>
            </div>

            {/* Divider */}
            <div className="mx-4" style={{ borderBottom: '1px solid hsl(var(--border))', opacity: 0.4 }} />

            {/* Feature toggles row */}
            <div className="flex items-center gap-2 px-4 py-2.5">
              {/* Model selector */}
              <div className="relative">
                <button
                  ref={modelBtnRef}
                  onClick={() => setModelOpen(!modelOpen)}
                  className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[12px] font-medium transition-all duration-200"
                  style={{
                    background: 'var(--ocean-50)',
                    border: '1px solid var(--ocean-200)',
                    color: 'var(--ocean-500)',
                  }}
                >
                  <ModelIcon modelKey={config.model_key} size={14} />
                  <span className="max-w-[100px] truncate">{currentModel?.label || config.model_key}</span>
                  <ChevronDown size={11} strokeWidth={2.5} className={`transition-transform duration-200 ${modelOpen ? 'rotate-180' : ''}`} />
                </button>
                {modelOpen && createPortal(
                  <>
                    <div className="fixed inset-0 z-[9998]" onClick={() => setModelOpen(false)} />
                    <div
                      className="fixed z-[9999] max-h-[240px] overflow-y-auto rounded-xl"
                      style={{
                        top: menuPos ? menuPos.top : 0,
                        left: menuPos ? menuPos.left : 0,
                        minWidth: menuPos ? menuPos.minWidth : 220,
                        background: 'rgba(255, 255, 255, 0.97)',
                        border: '1px solid hsl(var(--border))',
                        boxShadow: '0 16px 48px -12px rgba(15,31,56,0.18)',
                        transform: 'translateY(-100%)',
                        backdropFilter: 'blur(8px)',
                      }}
                    >
                      <div className="px-3 py-2 text-[9px] font-bold tracking-[0.14em] uppercase" style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.4, borderBottom: '1px solid hsl(var(--border))' }}>
                        选择模型
                      </div>
                      {sortedModels.map((model) => {
                        const active = model.key === config.model_key
                        return (
                          <button key={model.key} onClick={() => selectModel(model)}
                            className="w-full text-left px-3 py-2.5 text-[12px] flex items-center gap-2 transition-colors"
                            style={{ background: active ? 'var(--ocean-50)' : 'transparent', color: active ? 'var(--ocean-500)' : 'hsl(var(--foreground))' }}
                            onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = 'hsl(var(--muted))' }}
                            onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = 'transparent' }}
                          >
                            <ModelIcon modelKey={model.key} size={15} />
                            <span className="truncate">{model.label}</span>
                            {active && <div className="ml-auto w-1.5 h-1.5 rounded-full" style={{ background: 'var(--ocean-500)' }} />}
                          </button>
                        )
                      })}
                    </div>
                  </>,
                  document.body
                )}
              </div>

              {/* Thinking toggle */}
              <button
                onClick={toggleReasoning}
                disabled={!currentModel?.supports_reasoning}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[12px] font-medium transition-all duration-200 active:scale-[0.96]"
                style={{
                  background: config.enable_reasoning ? 'var(--ocean-50)' : 'transparent',
                  border: `1px solid ${config.enable_reasoning ? 'var(--ocean-200)' : 'hsl(var(--border))'}`,
                  color: config.enable_reasoning ? 'var(--ocean-500)' : 'hsl(var(--muted-foreground))',
                  opacity: config.enable_reasoning ? 1 : (!currentModel?.supports_reasoning ? 0.15 : 0.4),
                  cursor: !currentModel?.supports_reasoning ? 'not-allowed' : 'pointer',
                }}
                title="深度思考"
              >
                <Brain size={13} strokeWidth={1.8} />
                <span>思考</span>
              </button>

              {/* Search toggle */}
              <button
                onClick={toggleSearch}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[12px] font-medium transition-all duration-200 active:scale-[0.96]"
                style={{
                  background: config.enable_search ? 'var(--ocean-50)' : 'transparent',
                  border: `1px solid ${config.enable_search ? 'var(--ocean-200)' : 'hsl(var(--border))'}`,
                  color: config.enable_search ? 'var(--ocean-500)' : 'hsl(var(--muted-foreground))',
                  opacity: config.enable_search ? 1 : 0.4,
                  cursor: 'pointer',
                }}
                title="联网搜索"
              >
                <Globe size={13} strokeWidth={1.8} />
                <span>搜索</span>
              </button>

              {/* Hint */}
              <span className={`${isMobile ? 'hidden' : ''} ml-auto text-[10px] select-none`} style={{ color: 'hsl(var(--muted-foreground))', opacity: 0.2 }}>
                Enter ↵
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

function SparkleIcon({ size = 12 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" className="animate-star-spin-breathe">
      <path d="M12 0L13.5 8.5L22 6L15 12L22 18L13.5 15.5L12 24L10.5 15.5L2 18L9 12L2 6L10.5 8.5L12 0Z" fill="currentColor" />
    </svg>
  );
}