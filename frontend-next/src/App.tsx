import { AppShell } from '@/components/layout/AppShell'
import { Sidebar } from '@/components/sidebar/Sidebar'
import { Inspector } from '@/components/inspector/Inspector'
import { Composer } from '@/components/composer/Composer'
import { Thread } from '@/components/thread/Thread'
import { ThemeProvider } from '@/components/theme/ThemeProvider'
import { Toaster } from '@/components/ui/sonner'
import { MOCK_TRANSCRIPT } from '@/lib/mockTranscript'

export default function App() {
  return (
    <ThemeProvider>
      <AppShell
        sidebar={<Sidebar />}
        inspector={<Inspector />}
        main={
          <>
            <Thread transcript={MOCK_TRANSCRIPT} />
            <Composer onSend={(msg) => console.log('send', msg)} />
          </>
        }
      />
      <Toaster position="bottom-right" />
    </ThemeProvider>
  )
}
