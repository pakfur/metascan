const { Button, IconButton, Field, TextInput, Select, Textarea, Dialog, ConfirmBanner } = window.MetascanDesignSystem_f2fbbd

function ImportTextDialog({ onClose }) {
  const [text, setText] = React.useState('')
  const [confirming, setConfirming] = React.useState(false)
  return (
    <Dialog size="md" title="Import text" onDismiss={onClose}
      actions={confirming ? (
        <>
          <Button variant="dangerSolid" size="lg" onClick={onClose}>Replace structure</Button>
          <Button variant="secondary" size="lg" onClick={() => setConfirming(false)}>Cancel</Button>
        </>
      ) : (
        <>
          <Button variant="primary" size="lg" disabled={!text.trim()} onClick={() => setConfirming(true)}>Import</Button>
          <Button variant="secondary" size="lg" onClick={onClose}>Cancel</Button>
        </>
      )}>
      <p className="ms-hint" style={{ fontSize: 12, marginBottom: 14 }}>
        Paste your scene text — subjects, locations, one or two sentences per shot.
      </p>
      <Textarea variant="dialog" rows={12} placeholder="Paste scene text here…" value={text} onChange={(e) => setText(e.target.value)} />
      {confirming ? (
        <ConfirmBanner size="lg" message="This storyboard already has scenes. Re-parsing replaces all scenes, panels and hand-edited prompts." />
      ) : null}
    </Dialog>
  )
}

function SettingsDialog({ onClose }) {
  const SB = window.SB
  const t = SB.tree
  const [picker, setPicker] = React.useState(false)
  return (
    <Dialog size="md" title="Storyboard settings" onDismiss={onClose}
      actions={<Button variant="secondary" size="lg" onClick={onClose}>Close</Button>}>
      <section style={{ marginBottom: 22 }}>
        <h4 style={{ margin: '0 0 12px', fontSize: 14, fontWeight: 600 }}>Fields</h4>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <Field plainLabel label="Name"><TextInput variant="dialog" defaultValue={t.name} /></Field>
          <Field row>
            <Field plainLabel label="Aspect ratio"><Select variant="dialog" includeEmpty={false} options={SB.ASPECT_RATIOS} defaultValue={t.aspect_ratio} /></Field>
            <Field plainLabel label="Target model"><Select variant="dialog" includeEmpty={false} options={SB.TARGET_MODELS} defaultValue={t.target_model} /></Field>
          </Field>
          <Field plainLabel label="Workflow preset"><Select variant="dialog" placeholder="None" options={[{ value: 2, label: 'flux1 stills (t2i)' }]} defaultValue={2} /></Field>
          <Field row>
            <Field plainLabel label="Batch size"><TextInput variant="dialog" type="number" min="1" max="16" defaultValue={t.batch_size} /></Field>
            <Field plainLabel label="Base seed"><TextInput variant="dialog" type="number" defaultValue={t.base_seed} /></Field>
          </Field>
          <Field plainLabel label="Style block"><Textarea variant="dialog" rows={2} defaultValue={t.style_block} /></Field>
          <Field plainLabel label="Negative"><Textarea variant="dialog" rows={2} defaultValue={t.negative} /></Field>
          <Field row>
            <Field plainLabel label="Video target"><Select variant="dialog" placeholder="None" options={[{ value: 'minimax', label: 'MiniMax H3' }]} defaultValue="minimax" /></Field>
            <Field plainLabel label="Video mode"><Select variant="dialog" includeEmpty={false} options={SB.VIDEO_MODES} defaultValue={t.video_mode} /></Field>
          </Field>
          <Field plainLabel label="Video output directory"><Select variant="dialog" placeholder="Default" options={['/Users/jk/gws/metascan/assets/media']} /></Field>
          <Field row>
            <Field plainLabel label="Video name prefix"><TextInput variant="dialog" placeholder="{board}_{scene}_{shot}" /></Field>
            <Field plainLabel label="Image name prefix"><TextInput variant="dialog" placeholder="{board}_{shot}_{beat}" /></Field>
          </Field>
          <div style={{ display: 'flex', gap: 10 }}>
            <Button variant="primary" size="lg">Save</Button>
          </div>
        </div>
      </section>

      <section>
        <h4 style={{ margin: '0 0 12px', fontSize: 14, fontWeight: 600 }}>Subjects</h4>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          {t.subjects.map((s) => (
            <div key={s.id} style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: 12, border: '1px solid var(--surface-border)', borderRadius: 8, background: 'var(--surface-card)' }}>
              <div style={{ display: 'flex', gap: 8 }}>
                <TextInput variant="dialog" style={{ flex: '0 0 130px' }} defaultValue={s.name} />
                <TextInput variant="dialog" defaultValue={s.description} />
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <TextInput variant="dialog" style={{ flex: 1 }} placeholder="lora file…" defaultValue={s.lora_name ?? ''} />
                <TextInput variant="dialog" style={{ flex: '0 0 80px' }} type="number" step="0.05" defaultValue={s.lora_strength ?? 1} />
                <TextInput variant="dialog" style={{ flex: '0 0 110px' }} placeholder="voice" />
              </div>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                <div style={{ width: 56, height: 56, borderRadius: 6, border: '1px dashed var(--surface-border)', background: 'var(--surface-ground)', flexShrink: 0 }} />
                <div style={{ width: 56, height: 56, borderRadius: 6, border: '1px dashed var(--surface-border)', background: 'var(--surface-ground)', flexShrink: 0 }} />
                <Button size="md" onClick={() => setPicker(true)}>Choose reference…</Button>
                <Button size="md" variant="quiet">Describe with VLM</Button>
                <IconButton glyph="✕" destructive title="Remove subject" />
              </div>
            </div>
          ))}
          <Button variant="dashed" size="md" style={{ alignSelf: 'flex-start' }}>+ Subject</Button>
        </div>
      </section>
      {picker ? <window.ReferencePicker onClose={() => setPicker(false)} /> : null}
    </Dialog>
  )
}

function ReferencePicker({ onClose }) {
  const F = window.SB.F
  const [q, setQ] = React.useState('')
  const all = Array.from({ length: 18 }, (_, i) => ({ path: F(i + 1), name: '1414-A full body shot ' + (i + 1) + '.jpg' }))
  const shown = all.filter((m) => m.name.toLowerCase().includes(q.trim().toLowerCase()))
  return (
    <div className="ms-dialog-overlay ms-dialog-overlay--nested" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="ms-dialog ms-dialog--lg">
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
          <h4 style={{ margin: 0, fontSize: 14, flexShrink: 0, fontWeight: 600 }}>Choose a reference image</h4>
          <TextInput variant="dialog" placeholder="Filter by file name…" value={q} onChange={(e) => setQ(e.target.value)} />
          <IconButton variant="bare" glyph="×" title="Close" onClick={onClose} />
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))', gap: 10, overflowY: 'auto', minHeight: 120 }}>
          {shown.map((m) => (
            <button key={m.path} type="button" onClick={onClose}
              style={{ background: 'var(--surface-card)', border: '1px solid var(--surface-border)', borderRadius: 8, padding: 6, cursor: 'pointer', display: 'flex', flexDirection: 'column', gap: 4, textAlign: 'left' }}>
              <img src={m.path} alt="" style={{ width: '100%', aspectRatio: 1, objectFit: 'cover', borderRadius: 4, display: 'block' }} />
              <span style={{ fontSize: 11, color: 'var(--text-color-secondary)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{m.name}</span>
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

Object.assign(window, { ImportTextDialog, SettingsDialog, ReferencePicker })
