Shows which subjects are on screen in a beat, and which one leads. Order matters — the first subject is the primary and drives the prompt.

```jsx
<div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 4 }}>
  {beat.subject_ids.map((sid, i) => (
    <SubjectChip key={sid} name={subjectName(sid)} primary={i === 0} onClick={() => promote(sid)} />
  ))}
</div>
```

Membership is edited with the checkbox list below the chips; the chips only reorder. Do not add a remove X to a subject chip — that is the checklist's job.
