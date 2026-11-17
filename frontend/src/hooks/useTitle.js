import { useEffect } from 'react'

export default function useTitle(title) {
  useEffect(() => {
    document.title = title ? `${title} \u00b7 CineMatch` : 'CineMatch'
  }, [title])
}
