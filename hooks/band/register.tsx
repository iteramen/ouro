import type { Register, Timer } from 'claude-code'

// The line comes from bin/ouro-run-status.py, which reads the local progress file only: the band
// never reaches the network or gh, and draws nothing the script does not print.
export const register: Register = on => {
  let tick: Timer | undefined

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    tick?.cancel()
    tick = undefined
    // A survey holds the band alone: yield before the script runs or a redraw is armed.
    if (e.props.hasSurvey) {
      return next(e)
    }
    let line = ''
    try {
      const script = `${$.plugin.root}/bin/ouro-run-status.py`
      const { exitCode, stdout } = await $.process.run(['python3', script], {
        cwd: await $.session.cwd(),
        timeoutMs: 5000,
      })
      line = exitCode === 0 ? stdout.trim() : ''
    } catch {
      // No python3, or a script that hung: the slot stays empty, as for a repo with no run.
    }
    if (!line) {
      return next(e)
    }
    // Redraw while a line shows, so the bar moves and a finished summary clears on its own.
    tick = $.clock.after(10_000, () => $.ui.invalidate('ui.render'))
    const { Box, Text } = $.ui.resolve(e)
    return (
      <Box flexDirection="column">
        <Text dimColor>{line}</Text>
        {await next(e)}
      </Box>
    )
  })
}
