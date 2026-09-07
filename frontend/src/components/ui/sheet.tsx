import * as Dialog from "@radix-ui/react-dialog";
import { useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { Button } from "./button";
/**
 * The trigger toggles, so one press that reaches it twice — the double toggle
 * behind #205 — opens and immediately closes the sheet, leaving the trigger
 * marked expanded with nothing on screen. Holding the state here makes one
 * activation one decision. Two toggles in one render batch now both read the
 * same open state and agree on it; a duplicate that arrives a tick later is
 * turned away by this window, which is far shorter than any second press a
 * person makes.
 */
const SAME_ACTIVATION_MS = 80;
export function Sheet({
  title,
  description = "Details for your current campaign.",
  children,
  trigger,
}: {
  title: string;
  description?: string;
  children: ReactNode;
  trigger: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const openedAt = useRef(0);
  // Every way of dismissing the sheet closes it directly, so only the trigger
  // reaches this window and no deliberate close is ever held back by it.
  const toggle = (next: boolean) => {
    if (!next && performance.now() - openedAt.current < SAME_ACTIVATION_MS)
      return;
    if (next) openedAt.current = performance.now();
    setOpen(next);
  };
  const close = () => setOpen(false);
  return (
    <Dialog.Root open={open} onOpenChange={toggle}>
      <Dialog.Trigger asChild>{trigger}</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="sheet-overlay" />
        <Dialog.Content
          className="sheet-content"
          onEscapeKeyDown={close}
          onInteractOutside={close}
        >
          <Dialog.Title>{title}</Dialog.Title>
          <Dialog.Description>{description}</Dialog.Description>
          {children}
          <Button
            variant="outline"
            aria-label="Close details"
            className="sheet-close"
            onClick={close}
          >
            <X size={20} />
          </Button>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
