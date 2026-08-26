import React from 'react'

export function LoraListEditor({ label, entries = [], options = [], onChange, listId = 'ms-lora-options' }) {
  const [drafting, setDrafting] = React.useState(false)
  const emit = (next) => {
    if (onChange) onChange(next)
  }
  return (
    <div className="ms-lora">
      <label className="ms-label">{label}</label>
      {entries.map((entry, i) => (
        <div className="ms-lora__row" key={i + '-' + entry.name}>
          <input
            className="ms-control ms-lora__name"
            type="text"
            list={listId}
            defaultValue={entry.name}
            onChange={(e) => {
              const name = e.target.value.trim()
              if (!name) return
              emit(entries.map((en, j) => (j === i ? { ...en, name } : en)))
            }}
          />
          <input
            className="ms-control ms-lora__strength"
            type="number"
            step="0.05"
            title="Strength"
            defaultValue={entry.strength}
            onChange={(e) => {
              const strength = Number(e.target.value)
              if (!Number.isFinite(strength)) return
              emit(entries.map((en, j) => (j === i ? { ...en, strength } : en)))
            }}
          />
          <button
            type="button"
            className="ms-lora__remove"
            title="Remove"
            onClick={() => emit(entries.filter((_, j) => j !== i))}
          >
            {'\u2715'}
          </button>
        </div>
      ))}
      {drafting ? (
        <div className="ms-lora__row">
          <input
            className="ms-control ms-lora__name"
            type="text"
            list={listId}
            placeholder="lora file…"
            autoFocus
            onChange={(e) => {
              const name = e.target.value.trim()
              if (!name) return
              setDrafting(false)
              emit([...entries, { name, strength: 1.0 }])
            }}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setDrafting(false)
            }}
          />
          <button type="button" className="ms-lora__remove" title="Cancel" onClick={() => setDrafting(false)}>
            {'\u2715'}
          </button>
        </div>
      ) : (
        <button type="button" className="ms-btn ms-btn--dashed ms-btn--sm" style={{ alignSelf: 'flex-start' }} onClick={() => setDrafting(true)}>
          + Add LoRA
        </button>
      )}
      <datalist id={listId}>
        {options.map((o) => (
          <option key={o} value={o} />
        ))}
      </datalist>
    </div>
  )
}
