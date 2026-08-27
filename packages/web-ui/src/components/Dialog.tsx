import {
  Dialog as AriaDialog,
  DialogTrigger,
  Heading,
  Modal,
  ModalOverlay,
  type DialogProps as AriaDialogProps,
  type ModalOverlayProps,
} from "react-aria-components";
import type { ReactNode } from "react";
import { Button } from "./Button";

export { DialogTrigger };

export interface DialogProps extends Omit<AriaDialogProps, "children"> {
  title: string;
  description?: string;
  children: ReactNode | ((close: () => void) => ReactNode);
  size?: "sm" | "md" | "lg";
  /** Modal state control (uncontrolled when used inside DialogTrigger). */
  isOpen?: boolean;
  onOpenChange?: (open: boolean) => void;
  isDismissable?: boolean;
  modalProps?: Omit<ModalOverlayProps, "children">;
}

export function Dialog({ title, description, children, size = "md", isOpen, onOpenChange, isDismissable = true, modalProps, ...dialogProps }: DialogProps) {
  const overlayProps: ModalOverlayProps = {
    isDismissable,
    className: "cf-overlay",
    ...(isOpen !== undefined ? { isOpen } : {}),
    ...(onOpenChange ? { onOpenChange } : {}),
    ...modalProps,
  };
  return (
    <ModalOverlay {...overlayProps}>
      <Modal className={`cf-modal cf-modal--${size}`}>
        <AriaDialog {...dialogProps} className="cf-dialog">
          {({ close }) => (
            <>
              <header className="cf-dialog__header">
                <Heading slot="title" className="cf-dialog__title">
                  {title}
                </Heading>
                {isDismissable && (
                  <Button variant="ghost" size="sm" aria-label="Close" onPress={close}>
                    <span aria-hidden="true">×</span>
                  </Button>
                )}
              </header>
              {description && <p className="cf-dialog__description">{description}</p>}
              <div className="cf-dialog__body">{typeof children === "function" ? children(close) : children}</div>
            </>
          )}
        </AriaDialog>
      </Modal>
    </ModalOverlay>
  );
}
