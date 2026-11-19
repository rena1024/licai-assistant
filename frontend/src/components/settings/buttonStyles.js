// 设置各模块共用按钮尺寸与交互状态，颜色仅区分操作的重要性。
const base = 'inline-flex min-h-9 items-center justify-center gap-1.5 rounded-md border px-3 py-1.5 text-[13px] leading-5 font-medium transition-colors cursor-pointer focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:opacity-50 disabled:cursor-not-allowed'

export const settingsButton = {
  primary: `${base} border-accent bg-accent text-bg enabled:hover:border-accent-2 enabled:hover:bg-accent-2`,
  secondary: `${base} border-border bg-surface-3 text-text-dim enabled:hover:border-border-strong enabled:hover:bg-surface-raise enabled:hover:text-text`,
  danger: `${base} border-bear/40 bg-transparent text-bear enabled:hover:border-bear enabled:hover:bg-bear/10`,
}
