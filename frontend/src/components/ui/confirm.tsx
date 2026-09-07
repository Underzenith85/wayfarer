import * as Dialog from "@radix-ui/react-dialog";
import { Button } from "./button";
/**
 * The question asked before an action that cannot be taken back. A lifecycle
 * control on the setup panel sits a mis-click away from ending a campaign, so
 * the irreversible half of that pair asks first, names what it acts on and says
 * what happens (#163). Reversible actions do not use this: a confirmation there
 * is friction on a routine press, and an undo path serves better.
 *
 * Cancelling is the default answer. It is the first control in the dialog, so
 * the focus Radix moves inside on open lands on it, and Escape or a click on
 * the overlay reaches it too.
 */
export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel = "Cancel",
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  cancelLabel?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) onCancel();
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="sheet-overlay" />
        <Dialog.Content className="confirm-content" aria-label={title}>
          <Dialog.Title>{title}</Dialog.Title>
          <Dialog.Description>{description}</Dialog.Description>
          <div className="context-actions">
            <Button variant="outline" onClick={onCancel}>
              {cancelLabel}
            </Button>
            <Button variant="danger" disabled={busy} onClick={onConfirm}>
              {confirmLabel}
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
