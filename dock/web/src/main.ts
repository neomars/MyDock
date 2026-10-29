import { mount } from 'svelte'
import './app.css'
import App from './App.svelte'

// Pas de menu contextuel (appui long) ni de glisser-déposer : l'écran est un tableau de bord.
document.addEventListener('contextmenu', (event) => event.preventDefault())
document.addEventListener('dragstart', (event) => event.preventDefault())

mount(App, { target: document.getElementById('app')! })
