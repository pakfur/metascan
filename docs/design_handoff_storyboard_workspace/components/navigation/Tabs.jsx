import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function Tabs({ tabs = [], value, onChange, className, style }) {
  return (
    <nav className={cx('ms-tabs', className)} style={style}>
      {tabs.map((t) => {
        const key = typeof t === 'object' ? t.value : t
        const label = typeof t === 'object' ? t.label : t
        return (
          <button
            key={String(key)}
            type="button"
            className={cx('ms-tab', key === value && 'ms-tab--active')}
            onClick={() => onChange && onChange(key)}
          >
            {label}
          </button>
        )
      })}
    </nav>
  )
}
