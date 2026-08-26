The only tab pattern in the product: the storyboard side panel's Edit / Preview switch.

```jsx
<Tabs tabs={[{ value: 'edit', label: 'Edit' }, { value: 'preview', label: 'Preview' }]} value={tab} onChange={setTab} />
```

Two or three tabs at most, sentence-case labels, no icons, no counts. Metascan does not use tabs for primary navigation — that is the PrimeVue Menubar in the library view.
