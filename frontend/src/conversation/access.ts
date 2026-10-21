/**
 * The access token of each conversation this tab started. The backend only lets the holder of
 * a conversation's token resume it, so it lives in sessionStorage: a reload resumes the
 * conversation, another browser cannot.
 */

// A getter, because even reading `window.sessionStorage` throws where storage is blocked.
type TokenStorage = () => Pick<Storage, 'getItem' | 'setItem'>

const key = (conversationId: string) => `conversation-token:${conversationId}`

export function saveToken(storage: TokenStorage, conversationId: string, token: string): void {
  try {
    storage().setItem(key(conversationId), token)
  } catch {
    // Storage blocked: the conversation just can't be resumed after a reload.
  }
}

export function loadToken(storage: TokenStorage, conversationId: string): string | null {
  try {
    return storage().getItem(key(conversationId))
  } catch {
    return null
  }
}

export function socketUrl(
  page: { protocol: string; host: string },
  conversationId: string | null,
  token: string | null,
): string {
  const scheme = page.protocol === 'https:' ? 'wss' : 'ws'
  const path = conversationId
    ? `/ws/conversations/${conversationId}/?token=${encodeURIComponent(token ?? '')}`
    : '/ws/conversations/'
  return `${scheme}://${page.host}${path}`
}
