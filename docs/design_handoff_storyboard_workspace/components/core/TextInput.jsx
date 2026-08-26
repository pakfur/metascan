import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function TextInput({ variant = 'panel', mono = false, readOnly = false, className, ...rest }) {
  return (
    <input
      readOnly={readOnly}
      tabIndex={readOnly ? -1 : undefined}
      className={cx(
        'ms-control',
        variant === 'dialog' && 'ms-control--dialog',
        mono && 'ms-control--mono',
        readOnly && 'ms-control--readonly',
        className,
      )}
      {...rest}
    />
  )
}
