import { useState } from 'react'
import { api } from '../hooks/api'
import Button from './Button'
import Modal from './Modal'
import { useToast } from './Toast'
import styles from './EditProfileModal.module.css'

export default function EditProfileModal({ profile, onClose, onSaved }) {
  const toast = useToast()
  const [displayName, setDisplayName] = useState(profile.display_name || '')
  const [bio, setBio] = useState(profile.bio || '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  const submit = async (event) => {
    event.preventDefault()
    setError('')
    setSaving(true)
    try {
      await api.updateProfile({
        display_name: displayName.trim() || undefined,
        bio: bio.trim(),
      })
      const nextName = displayName.trim() || profile.display_name
      onSaved?.({ ...profile, display_name: nextName, bio: bio.trim() })
      toast('Profile updated.')
      onClose()
    } catch (err) {
      setError(err.message)
      toast(err.message, 'error')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal title="Edit profile" onClose={onClose}>
      <form className={styles.form} onSubmit={submit}>
        <label className={styles.field} htmlFor="profile-name">
          <span className={styles.label}>Display name</span>
          <input
            id="profile-name"
            className={styles.input}
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            maxLength={60}
            placeholder="Your name, as it should appear"
            data-autofocus
          />
        </label>

        <label className={styles.field} htmlFor="profile-bio">
          <span className={styles.label}>Bio</span>
          <textarea
            id="profile-bio"
            className={styles.textarea}
            value={bio}
            onChange={(event) => setBio(event.target.value)}
            maxLength={400}
            rows={4}
            placeholder="A sentence about the films you keep coming back to."
          />
        </label>

        <p className={styles.handleNote}>Your handle stays @{profile.username}.</p>

        {error ? (
          <p className={styles.error} role="alert">
            {error}
          </p>
        ) : null}

        <div className={styles.actions}>
          <Button variant="quiet" onClick={onClose} disabled={saving}>
            Cancel
          </Button>
          <Button type="submit" disabled={saving}>
            {saving ? 'Saving…' : 'Save changes'}
          </Button>
        </div>
      </form>
    </Modal>
  )
}
