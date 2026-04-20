import { createApp } from 'vue'
import { createPinia } from 'pinia'
import PrimeVue from 'primevue/config'
import Aura from '@primevue/themes/aura'
import Tooltip from 'primevue/tooltip'
import Button from 'primevue/button'
import InputText from 'primevue/inputtext'
import InputGroup from 'primevue/inputgroup'
import Menubar from 'primevue/menubar'
import AutoComplete from 'primevue/autocomplete'
import 'primeicons/primeicons.css'
import './style.css'
import App from './App.vue'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(PrimeVue, {
  theme: {
    preset: Aura,
  },
})

app.directive('tooltip', Tooltip)
app.component('Button', Button)
app.component('InputText', InputText)
app.component('InputGroup', InputGroup)
app.component('Menubar', Menubar)
app.component('AutoComplete', AutoComplete)

app.mount('#app')
