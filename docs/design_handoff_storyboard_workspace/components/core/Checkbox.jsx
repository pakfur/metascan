import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function Checkbox({ label, size = 'sm', className, style, ...rest }) {
  return (
    <label className={cx('ms-check', size === 'md' && 'ms-check--lg', className)} style={style}>
      <input type="checkbox" {...rest} />
      {label}
    </label>
  )
}
