import { AppShell } from '@/components/layout/AppShell'
import { Sidebar } from '@/components/sidebar/Sidebar'
import { Inspector } from '@/components/inspector/Inspector'
import { Composer } from '@/components/composer/Composer'
import { EmptyThread } from '@/components/thread/EmptyThread'
import { ThemeProvider } from '@/components/theme/ThemeProvider'
import { Toaster } from '@/components/ui/sonner'

export default function App() {
  return (
    <ThemeProvider>
      <AppShell
        sidebar={<Sidebar />}
        inspector={<Inspector />}
        main={
          <>
            <EmptyThread />
            <Composer onSend={(msg) => console.log('send', msg)} />
          </>
        }
      />
      <Toaster position="bottom-right" />
    </ThemeProvider>
  )
}
