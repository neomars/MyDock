// La fenêtre « Vidéos de l'économiseur » : les adresses d'où le dock télécharge, et où ça en est. Ouverte depuis les Réglages.

class SaverSourcesState {
  open = $state(false)

  show(): void {
    this.open = true
  }

  close(): void {
    this.open = false
  }
}

export const saverSources = new SaverSourcesState()
