import * as Dialog from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { X } from "lucide-react";
import { Button } from "./button";
export function Sheet({
  title,
  children,
  trigger,
}: {
  title: string;
  children: ReactNode;
  trigger: ReactNode;
}) {
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>{trigger}</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="sheet-overlay" />
        <Dialog.Content className="sheet-content">
          <Dialog.Title>{title}</Dialog.Title>
          <Dialog.Description>
            Details for your current campaign.
          </Dialog.Description>
          {children}
          <Dialog.Close asChild>
            <Button
              variant="outline"
              aria-label="Close details"
              className="sheet-close"
            >
              <X size={20} />
            </Button>
          </Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
