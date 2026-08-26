Multi-line input: beat action, sound, dialog lines, prompts, premise, outline JSON.

```jsx
<Textarea rows={3} defaultValue={beat.action} onChange={onActionChange} />
<Textarea rows={8} mono placeholder="No video prompt compiled yet." />
<Textarea variant="dialog" rows={10} mono value={outlineJson} />
```

Always `resize: vertical` (never both, never none). Placeholders are full sentences with a period when they explain absence ("No video prompt compiled yet."), bare phrases when they hint at input ("spoken line", "voice").
