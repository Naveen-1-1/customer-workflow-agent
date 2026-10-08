import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from 'react-router'
import { DepsProvider } from '@/api/context'
import { Toaster } from '@/components/ui/sonner'
import { router } from '@/router'
import './index.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <DepsProvider>
      <RouterProvider router={router} />
      <Toaster />
    </DepsProvider>
  </StrictMode>,
)
