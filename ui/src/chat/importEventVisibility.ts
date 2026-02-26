function resolveSearch(search?: string): string {
  if (typeof search === 'string') {
    return search
  }
  if (typeof window === 'undefined') {
    return ''
  }
  return window.location.search
}

export function shouldRenderImportEvent(eventKind: string, search?: string): boolean {
  if (eventKind !== 'debug') {
    return true
  }

  const params = new URLSearchParams(resolveSearch(search))
  return params.get('debug') === '1'
}
