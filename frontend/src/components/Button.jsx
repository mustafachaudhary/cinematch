import styles from './Button.module.css'

export default function Button({
  variant = 'primary',
  size = 'md',
  full = false,
  className = '',
  type = 'button',
  children,
  ...props
}) {
  const classes = [
    styles.button,
    styles[variant] || styles.primary,
    size === 'sm' ? styles.sm : '',
    full ? styles.full : '',
    className,
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <button type={type} className={classes} {...props}>
      {children}
    </button>
  )
}
