// Écran d'accueil : un gros bouton par page, comme les touches d'un Stream Deck. La taille n'est pas écrite en dur :
// on remplit la place disponible avec le plus de surface par bouton, sans descendre sous ce qu'il faut pour que le
// libellé (écrit en gros : l'écran est petit) et l'icône tiennent. La grille s'adapte au nombre de pages choisies
// dans les Réglages (neuf boutons en 3 × 3, quatre en 2 × 2, trois en une rangée…).

export interface HomeLayout {
  cols: number
  rows: number
  /** Largeur et hauteur d'un bouton, en pixels. */
  width: number
  height: number
  gap: number
}

export interface HomeOptions {
  /** Espace entre deux boutons : assez pour que le doigt ne touche jamais deux touches à la fois. */
  gap?: number
  /** En dessous, « Alimentation » ne tient plus sur une ligne en 36 px gras. */
  minWidth?: number
  /** En dessous, l'icône et le libellé ne tiennent plus l'un sur l'autre. */
  minHeight?: number
  /** Au-delà, un seul bouton énorme au milieu de l'écran n'a plus rien d'un clavier. */
  maxWidth?: number
  maxHeight?: number
}

export const HOME_DEFAULTS = { gap: 14, minWidth: 300, minHeight: 110, maxWidth: 520, maxHeight: 260 } as const

/**
 * La grille de `count` boutons dans `width` × `height` pixels. Parmi les arrangements assez grands, on prend celui qui
 * donne le plus de surface à chaque bouton ; à surface égale, moins de cases vides, puis plus de colonnes (l'écran est
 * large). Si aucun n'est assez grand (trop de boutons pour la place), on prend le moins serré. Rien n'est calculé tant
 * que la place n'est pas connue.
 */
export function homeLayout(count: number, width: number, height: number, options: HomeOptions = {}): HomeLayout {
  const o = { ...HOME_DEFAULTS, ...options }
  const empty: HomeLayout = { cols: 1, rows: 1, width: 0, height: 0, gap: o.gap }
  if (count <= 0 || width <= 0 || height <= 0) return empty

  type Candidate = HomeLayout & { spare: number; room: number }
  let best: Candidate | null = null
  for (let cols = 1; cols <= count; cols++) {
    const rows = Math.ceil(count / cols)
    const w = Math.max(0, Math.floor((width - (cols - 1) * o.gap) / cols))
    const h = Math.max(0, Math.floor((height - (rows - 1) * o.gap) / rows))
    const candidate: Candidate = {
      cols,
      rows,
      width: Math.min(w, o.maxWidth),
      height: Math.min(h, o.maxHeight),
      gap: o.gap,
      spare: cols * rows - count,
      // Combien de fois le minimum exigé tient dans la place : au moins 1 pour un arrangement acceptable.
      room: Math.min(w / o.minWidth, h / o.minHeight),
    }
    if (best === null || better(candidate, best)) best = candidate
  }
  const { spare: _spare, room: _room, ...layout } = best ?? { ...empty, spare: 0, room: 0 }
  return layout
}

type Ranked = HomeLayout & { spare: number; room: number }

function better(a: Ranked, b: Ranked): boolean {
  const okA = a.room >= 1
  const okB = b.room >= 1
  if (okA !== okB) return okA
  if (!okA) return a.room > b.room // aucun n'est assez grand : le moins serré
  const areaA = a.width * a.height
  const areaB = b.width * b.height
  if (areaA !== areaB) return areaA > areaB
  if (a.spare !== b.spare) return a.spare < b.spare
  return a.cols > b.cols
}

/** La largeur que prend la grille : le conteneur s'y ajuste pour que la dernière rangée, incomplète, se centre. */
export function gridWidth(layout: HomeLayout): number {
  return layout.cols * layout.width + (layout.cols - 1) * layout.gap
}
