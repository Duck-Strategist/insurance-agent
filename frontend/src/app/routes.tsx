import type { RouteObject } from 'react-router'

import { AppLayout } from '../components/AppLayout'
import { RequireAdmin } from '../components/RequireAdmin'
import { AdminDocumentsPage } from '../pages/AdminDocumentsPage'
import { HomePage } from '../pages/HomePage'
import { LoginPage } from '../pages/LoginPage'
import { NotFoundPage } from '../pages/NotFoundPage'
import { ProductDetailPage } from '../pages/ProductDetailPage'
import { ProductsPage } from '../pages/ProductsPage'

export const routes: RouteObject[] = [
  {
    element: <AppLayout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'login', element: <LoginPage /> },
      { path: 'products', element: <ProductsPage /> },
      { path: 'products/:id', element: <ProductDetailPage /> },
      {
        path: 'admin/documents',
        element: (
          <RequireAdmin>
            <AdminDocumentsPage />
          </RequireAdmin>
        ),
      },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]
