import * as React from "react"
import * as SwitchPrimitives from "@radix-ui/react-switch"

import { cn } from "@/lib/utils"

const Switch = React.forwardRef(({ className, ...props }, ref) => (
  <SwitchPrimitives.Root
    className={cn(
      "peer inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border border-[var(--prism-border-default)] bg-[var(--prism-bg-elevated)] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-[var(--prism-accent)]/40 focus-visible:ring-offset-0 disabled:cursor-not-allowed disabled:opacity-40 data-[state=checked]:bg-[var(--prism-accent-subtle)] data-[state=unchecked]:bg-[var(--prism-bg-elevated)]",
      className
    )}
    {...props}
    ref={ref}>
      <SwitchPrimitives.Thumb
        className={cn(
          "pointer-events-none block h-3.5 w-3.5 rounded-full bg-[var(--prism-accent)] shadow-sm ring-0 transition-transform data-[state=checked]:translate-x-[18px] data-[state=unchecked]:translate-x-[2px]"
        )} />
  </SwitchPrimitives.Root>
))
Switch.displayName = SwitchPrimitives.Root.displayName

export { Switch }
