// Le sélecteur d'applications du lanceur : une fenêtre ouverte depuis la page Applis ou les Réglages.

class PickerState {
  open = $state(false)

  show(): void {
    this.open = true
  }

  hide(): void {
    this.open = false
  }
}

export const picker = new PickerState()
