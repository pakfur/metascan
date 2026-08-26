Wraps every labelled control in the storyboard panes and dialogs.

```jsx
<Field label="Action"><Textarea rows={3} defaultValue={beat.action} /></Field>

<Field label="Prompt" aside={<><span className="ms-hint" style={{ flex: 1 }}>synthesized</span><Button variant="link">Unlock</Button></>}>
  <Textarea rows={4} mono />
</Field>

<Field row>
  <Field label="Shot size"><Select options={SHOT_SIZES} /></Field>
  <Field label="Angle"><Select options={ANGLES} /></Field>
  <Field label="Lens"><Select options={LENSES} /></Field>
</Field>
```

Use `plainLabel` inside dialogs (Create storyboard, Compose story) — that is the product's split: uppercase eyebrows in the working panes, sentence-case labels in modal forms.
