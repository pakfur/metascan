Native checkbox plus its label. Metascan does not restyle the box itself.

```jsx
<div className="ms-checklist">
  {subjects.map(s => (
    <Checkbox key={s.id} label={s.name} checked={beat.subject_ids.includes(s.id)} onChange={() => toggleSubject(s.id)} />
  ))}
</div>

<Checkbox size="md" label="Beats" checked={stageChecks.beats} onChange={...} />
```

Wrap groups in `.ms-checklist` (flex-wrap, 4px/12px gap) for the inline subject list, or a flex row with 16px gap for the dialog stage picker.
