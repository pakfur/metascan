The storyboard landing list. The list itself is a plain 720px column — no table, no cards grid.

```jsx
<div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
  {boards.map(b => (
    <BoardRow
      key={b.id}
      name={b.name}
      meta={b.aspect_ratio + ' · ' + b.target_model + ' · updated ' + formatDate(b.updated_at)}
      actions={<>
        <Button onClick={() => open(b.id)}>Open</Button>
        <Button variant="danger" onClick={() => confirmDelete(b)}>Delete</Button>
      </>}
    />
  ))}
</div>
```

Empty list is a dashed 10px-radius box reading "No storyboards yet — create one and paste your scene text." Deleting a board always asks what happens to its generated images.
