import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, ApiError } from '@/api/client'
import { chatLoader } from './router'

const load = (url: string) =>
  chatLoader({ request: new Request(url), params: {}, context: {} } as never) as Promise<Response | null>

describe('chatLoader', () => {
  afterEach(() => vi.restoreAllMocks())

  it('sends the customer to the busy page when the app is full', async () => {
    vi.spyOn(api, 'createChat').mockRejectedValue(new ApiError(503, 'at_capacity', 'busy'))
    const res = await load('http://test/?new=1')
    expect(res?.headers.get('Location')).toBe('/?busy=1')
  })

  it('stays on the busy page until asked to try again', async () => {
    const create = vi.spyOn(api, 'createChat')
    expect(await load('http://test/?busy=1')).toBeNull()
    expect(create).not.toHaveBeenCalled()
  })

  it('still fails loudly for other errors', async () => {
    vi.spyOn(api, 'createChat').mockRejectedValue(new ApiError(500, 'http_error', 'boom'))
    await expect(load('http://test/?new=1')).rejects.toThrow('boom')
  })
})
