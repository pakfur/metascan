import { createRouter, createWebHashHistory } from 'vue-router'
import LibraryView from '../views/LibraryView.vue'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'library', component: LibraryView },
    {
      path: '/storyboard/:id?',
      name: 'storyboard',
      component: () => import('../views/StoryboardView.vue'),
      props: (route) => ({ id: route.params.id ? Number(route.params.id) : undefined }),
    },
  ],
})
export default router
