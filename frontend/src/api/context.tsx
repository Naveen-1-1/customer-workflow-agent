import { createContext, useContext, type ReactNode } from 'react'
import { api, type Api } from './client'

export interface Deps {
  api: Api
  openEvents: (url: string) => EventSource
}

const defaultDeps: Deps = { api, openEvents: (url) => new EventSource(url) }
const DepsContext = createContext<Deps>(defaultDeps)

// Tests swap in a fake API and a fake EventSource here.
export function DepsProvider({ value, children }: { value?: Partial<Deps>; children: ReactNode }) {
  return <DepsContext value={{ ...defaultDeps, ...value }}>{children}</DepsContext>
}

export const useDeps = () => useContext(DepsContext)
