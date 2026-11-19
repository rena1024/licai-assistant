import AdminUsers from './AdminUsers'
import ModelSettings from './ModelSettings'
import UsagePanel from './UsagePanel'
import DataCenter from './DataCenter'
import OperationsPanel from './OperationsPanel'
import NotificationSettings from './NotificationSettings'
import { ProxySection } from './settings/ProxySection'
import { ProviderSection } from './settings/ProviderSection'

export default function AdminPage({ admin, page, title }) {
  // 先检查身份再挂载管理表单，普通账号即使打开页面链接也不会请求管理数据。
  if (!admin) return <p role="alert" className="rounded-xl border border-border bg-surface-2 p-4">此页面仅管理员可访问，请使用管理员账号登录。</p>
  const pages = {
    'admin-users': () => <AdminUsers />,
    'admin-models': () => <ModelSettings admin managementOnly />,
    'admin-usage': () => <UsagePanel admin />,
    'admin-data': () => <DataCenter />,
    'admin-system': () => <>
      <ProxySection />
      <div className="border-t border-border pt-4"><ProviderSection admin managementOnly /></div>
      <NotificationSettings admin managementOnly showMessages={false} />
    </>,
    'admin-operations': () => <OperationsPanel />,
  }
  return <section className="rounded-xl border border-accent/20 bg-surface-2/80 overflow-hidden" aria-labelledby="admin-page-title">
    <div className="px-4 py-3 border-b border-border"><h2 id="admin-page-title" className="text-base font-semibold text-accent">{title}</h2></div>
    <div className="p-4 space-y-4">{pages[page] ? pages[page]() : <p role="alert">管理页面不存在。</p>}</div>
  </section>
}
