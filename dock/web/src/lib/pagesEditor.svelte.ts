// La fenêtre « Pages et ordre » : ouverte depuis les Réglages.

class PagesEditorState {
  open = $state(false)

  edit(): void {
    this.open = true
  }

  close(): void {
    this.open = false
  }
}

export const pagesUi = new PagesEditorState()
