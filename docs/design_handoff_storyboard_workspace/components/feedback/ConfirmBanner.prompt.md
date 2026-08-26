Used when a server call comes back with confirm_required and the decision belongs next to the control, not in a modal — re-beating a shot whose beats already have images, or re-running a compose stage that would overwrite content.

```jsx
<ConfirmBanner
  message="Beats have generated images or locked prompts — recompose anyway?"
  actions={<>
    <Button variant="danger" size="sm">Continue</Button>
    <Button size="sm">Cancel</Button>
  </>}
/>
```

The copy always states what will be lost, then asks in the same sentence. Use `size="lg"` inside a dialog (Compose story: "This replaces existing content — continue?").
