<script lang="ts">
  // Le dessin du bouton Spotify : un rond et trois arcs, tracés comme les icônes de la bibliothèque (même API : taille, épaisseur).
  let { size = 24, strokeWidth = 2, ...rest }: { size?: number; strokeWidth?: number; [attribute: string]: unknown } = $props()
</script>

<svg
  xmlns="http://www.w3.org/2000/svg"
  width={size}
  height={size}
  viewBox="0 0 24 24"
  fill="none"
  stroke="currentColor"
  stroke-width={strokeWidth}
  stroke-linecap="round"
  stroke-linejoin="round"
  {...rest}
>
  <circle cx="12" cy="12" r="10" />
  <path d="M6.4 9c4.1-1.2 8.3-.8 11.7 1.3" />
  <path d="M7.2 12.4c3.3-.9 6.6-.6 9.5 1.1" />
  <path d="M8.1 15.7c2.6-.7 5.1-.4 7.3.9" />
</svg>
