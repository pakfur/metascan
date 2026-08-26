Adding a scene or a shot is a tile at the end of the row, not a toolbar button.

```jsx
<AddTile kind="scene" label="Scene" onClick={openSceneCreator} />

<AddTile kind="panel" label="Panel" onClick={startAdd}>
  {adding && <>
    <TextInput placeholder="Action" autoFocus onKeyDown={submitOnEnter} />
    <div style={{ display: 'flex', gap: 6, marginTop: 6 }}>
      <Button size="xs">Add</Button>
      <Button size="xs">Cancel</Button>
    </div>
  </>}
</AddTile>
```

The panel variant commits inline — one field, Enter to add, Escape to cancel. Do not route it through a dialog; only scenes get a dialog because they carry many fields.
