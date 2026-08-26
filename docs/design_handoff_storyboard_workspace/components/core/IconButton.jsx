import React from 'react'
const cx = (...a) => a.filter(Boolean).join(' ')

export function IconButton({
  glyph,
  icon,
  variant = 'outline',
  size,
  destructive = false,
  disabled = false,
  title,
  ariaLabel,
  className,
  style,
  onClick,
  ...rest
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      title={title}
      aria-label={ariaLabel ?? title}
      onClick={onClick}
      style={style}
      className={cx(
        'ms-iconbtn',
        'ms-iconbtn--' + variant,
        size === 'lg' && 'ms-iconbtn--lg',
        destructive && 'ms-iconbtn--destructive',
        className,
      )}
      {...rest}
    >
      {icon ? <span className={cx('pi', icon)} aria-hidden="true" /> : glyph}
    </button>
  )
}
