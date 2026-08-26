import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function Select({ options = [], placeholder = '\u2014', includeEmpty = true, variant = 'panel', className, children, ...rest }) {
  return (
    <select className={cx('ms-control', variant === 'dialog' && 'ms-control--dialog', className)} {...rest}>
      {children ?? (
        <>
          {includeEmpty ? <option value="">{placeholder}</option> : null}
          {options.map((o) => {
            const value = typeof o === 'object' ? o.value : o
            const label = typeof o === 'object' ? o.label : o
            return (
              <option key={String(value)} value={value}>
                {label}
              </option>
            )
          })}
        </>
      )}
    </select>
  )
}
