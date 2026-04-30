/**
 * Settings-page wire types — mirror backend/api/schemas/settings.py and
 * backend/api/schemas/oauth_codex.py.
 */

export interface ApiKeyStatus {
  provider: string
  display_name: string
  has_key: boolean
  updated_at: string | null
  credential_shape: 'api_key' | 'codex_oauth' | string
  email: string | null
  expires_at: number | null
}

export interface IdentitySummary {
  provider: string
  display: string
  linked_at: string
  removable: boolean
}

export interface CodexOAuthStartResponse {
  pending_id: string
  user_code: string
  verification_url: string
  expires_in: number
}

export interface CodexOAuthStatusResponse {
  status: 'pending' | 'complete' | 'expired' | 'error' | string
  email: string | null
  error: string | null
}
