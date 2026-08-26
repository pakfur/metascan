The modal shell behind every storyboard dialog: New storyboard, Compose story, Import text, Scene edit, Settings, Workflow presets, the reference picker, and the three-way delete confirm.

```jsx
<Dialog
  title='Delete scene "Kitchen, dawn"?'
  message="Its panels have generated images. Delete them permanently, or keep them visible in the media library?"
  meta="12 generated images affected."
  onDismiss={cancel}
  actions={<>
    <Button variant="danger" size="lg">Delete images permanently</Button>
    <Button variant="primary" size="lg">Keep images in library</Button>
    <Button variant="secondary" size="lg">Cancel</Button>
  </>}
/>
```

The three-way destructive confirm is a Metascan signature: purge / keep-in-library / cancel, with the safe option styled primary and the destructive one merely outlined. Never collapse it to a two-button OK/Cancel. Dialogs never nest each other — a dialog that needs another one closes itself and asks its parent to open the next.
