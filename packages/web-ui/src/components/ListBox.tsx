import {
  ListBox as AriaListBox,
  ListBoxItem as AriaListBoxItem,
  type ListBoxProps as AriaListBoxProps,
  type ListBoxItemProps as AriaListBoxItemProps,
} from "react-aria-components";

export function ListBox<T extends object>({ className, ...props }: AriaListBoxProps<T>) {
  return <AriaListBox {...props} className={["cf-listbox", typeof className === "string" ? className : ""].join(" ").trim()} />;
}

export function ListBoxItem(props: AriaListBoxItemProps) {
  const text = typeof props.children === "string" ? props.children : undefined;
  return <AriaListBoxItem {...props} {...(text && !props.textValue ? { textValue: text } : {})} className="cf-listbox__item" />;
}
