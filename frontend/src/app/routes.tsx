import type { RouteObject } from 'react-router'

import { AppLayout } from '../components/AppLayout'
import { RequireAdmin } from '../components/RequireAdmin'
import { RequireLogin } from '../components/RequireLogin'
import { AdminDocumentsPage } from '../pages/AdminDocumentsPage'
import { AdminEvalRunsPage } from '../pages/AdminEvalRunsPage'
import { AdminSearchPage } from '../pages/AdminSearchPage'
import { ChatPage } from '../pages/ChatPage'
import { HomePage } from '../pages/HomePage'
import { LoginPage } from '../pages/LoginPage'
import { NotFoundPage } from '../pages/NotFoundPage'
import { ProductDetailPage } from '../pages/ProductDetailPage'
import { ProductsPage } from '../pages/ProductsPage'
import { PlansPage } from '../pages/PlansPage'
import { QuotesPage } from '../pages/QuotesPage'

export const routes: RouteObject[] = [
  {
    element: <AppLayout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'login', element: <LoginPage /> },
      { path: 'products', element: <ProductsPage /> },
      { path: 'products/:id', element: <ProductDetailPage /> },
      {
        // 可选参数：新对话拿到 id 后跳到 /chat/:id 时页面不重新挂载，流式状态不中断
        path: 'chat/:threadId?',
        element: (
          <RequireLogin>
            <ChatPage />
          </RequireLogin>
        ),
      },
      {
        path: 'plans',
        element: (
          <RequireLogin>
            <PlansPage />
          </RequireLogin>
        ),
      },
      {
        path: 'quotes',
        element: (
          <RequireLogin>
            <QuotesPage />
          </RequireLogin>
        ),
      },
      {
        path: 'admin/documents',
        element: (
          <RequireAdmin>
            <AdminDocumentsPage />
          </RequireAdmin>
        ),
      },
      {
        path: 'admin/search',
        element: (
          <RequireAdmin>
            <AdminSearchPage />
          </RequireAdmin>
        ),
      },
      {
        path: 'admin/evals',
        element: (
          <RequireAdmin>
            <AdminEvalRunsPage />
          </RequireAdmin>
        ),
      },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]
