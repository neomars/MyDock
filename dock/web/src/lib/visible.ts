// Action Svelte : appelle `callback` quand l'élément entre dans l'écran (jaquettes demandées au fil du défilement).

export function whenVisible(node: HTMLElement, callback: () => void): { destroy(): void } {
  const observer = new IntersectionObserver(
    (entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        callback()
        observer.disconnect()
      }
    },
    { rootMargin: '200px' }, // un peu avant : l'image est là quand on arrive
  )
  observer.observe(node)
  return { destroy: () => observer.disconnect() }
}
