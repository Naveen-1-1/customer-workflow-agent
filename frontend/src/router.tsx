import { createBrowserRouter, redirect, useRouteError, type LoaderFunctionArgs } from 'react-router'
import { api, ApiError } from '@/api/client'
import { ChatPage, LAST_CHAT_KEY } from '@/pages/ChatPage'
import { SupervisorPage } from '@/pages/SupervisorPage'

function lastChat(): string | null {
  try {
    return localStorage.getItem(LAST_CHAT_KEY)
  } catch {
    return null
  }
}

// Decide which chat to show before rendering (so React's dev double-render can't create two).
export async function chatLoader({ request }: LoaderFunctionArgs) {
  const url = new URL(request.url)
  const wantsNew = url.searchParams.has('new')
  if ((url.searchParams.get('chat') || url.searchParams.has('busy')) && !wantsNew) return null
  const last = wantsNew ? null : lastChat()
  if (last) {
    try {
      await api.getChat(last)
      return redirect(`/?chat=${last}`)
    } catch {
      /* gone (e.g. after a reset): start a new one */
    }
  }
  try {
    const chat = await api.createChat()
    return redirect(`/?chat=${chat.chat_id}`)
  } catch (err) {
    if (err instanceof ApiError && err.code === 'at_capacity') return redirect('/?busy=1')
    throw err
  }
}

function RouteError() {
  const error = useRouteError() as Error | undefined
  return (
    <div className="mx-auto max-w-xl p-6 text-sm">
      <h1 className="mb-2 text-lg font-semibold">Can't reach the backend</h1>
      <p className="text-muted-foreground">
        Start it with <code>make dev</code> (or <code>make dev-api</code>), then reload.
      </p>
      {error?.message && <p className="text-muted-foreground mt-2">{error.message}</p>}
    </div>
  )
}

export const router = createBrowserRouter([
  { path: '/', loader: chatLoader, element: <ChatPage />, errorElement: <RouteError /> },
  { path: '/supervisor', element: <SupervisorPage />, errorElement: <RouteError /> },
])
