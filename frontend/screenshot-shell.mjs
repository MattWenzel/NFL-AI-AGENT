import { chromium } from 'playwright'
import { mkdir } from 'node:fs/promises'

const OUT_DIR = '/home/mattw813/Documents/NFL_AI_AGENT/.design/chat-workspace/screenshots'
const APP = 'http://localhost:5173'
const EMAIL = process.env.PREVIEW_EMAIL || 'preview@local.test'
const PASSWORD = process.env.PREVIEW_PASSWORD || 'preview-password-123'
const INVITE = process.env.REGISTRATION_INVITE_CODE || ''

async function ensureSignedIn(page) {
  const status = await page.request.get(`${APP}/auth/status`)
  const sb = await status.json()
  if (sb.authenticated) return
  const loginRes = await page.request.post(`${APP}/auth/login`, {
    data: { email: EMAIL, password: PASSWORD },
  })
  if (loginRes.ok()) return
  if (!INVITE) throw new Error('login failed and no invite code in env')
  const regRes = await page.request.post(`${APP}/auth/register`, {
    data: { email: EMAIL, password: PASSWORD, invite_code: INVITE },
  })
  if (!regRes.ok()) throw new Error(`register failed: ${regRes.status()}`)
}

async function shoot(browser, mode, bp, name, action) {
  const ctx = await browser.newContext({
    viewport: { width: bp.width, height: bp.height },
    deviceScaleFactor: 2,
    colorScheme: mode === 'dark' ? 'dark' : 'light',
  })
  await ctx.addInitScript((m) => {
    try {
      window.localStorage.setItem('chat-workspace.theme', m)
    } catch {}
  }, mode)
  const page = await ctx.newPage()
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.log(`[browser-error ${name}/${mode}]`, msg.text())
  })
  await ensureSignedIn(page)
  await page.goto(APP, { waitUntil: 'networkidle', timeout: 15000 })
  await page.waitForTimeout(900)
  if (action) await action(page)
  const path = `${OUT_DIR}/v2-${name}-${mode}-${bp.name}.png`
  await page.screenshot({ path, fullPage: false })
  console.log(`wrote v2-${name}-${mode}-${bp.name}.png`)
  await ctx.close()
}

async function main() {
  await mkdir(OUT_DIR, { recursive: true })
  const browser = await chromium.launch()

  const desktop = { name: 'desktop', width: 1440, height: 900 }
  await shoot(browser, 'dark', desktop, 'shell-empty', null)
  await shoot(browser, 'light', desktop, 'shell-empty', null)

  // If preview user has any conversations, load the first one for thread eyeball.
  await shoot(browser, 'dark', desktop, 'thread', async (page) => {
    const firstRow = page.locator('aside button[type="button"]').filter({
      hasText: /turns?$/i,
    }).first()
    const count = await firstRow.count()
    if (count > 0) {
      await firstRow.click()
      await page.waitForTimeout(1500)
    }
  })

  await browser.close()
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
