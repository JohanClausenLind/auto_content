import {
  TextField as AriaTextField,
  FieldError,
  Input,
  Label,
  Text,
  type TextFieldProps as AriaTextFieldProps,
  type ValidationResult,
} from "react-aria-components";

export interface TextFieldProps extends Omit<AriaTextFieldProps, "children"> {
  label: string;
  description?: string;
  placeholder?: string;
  errorMessage?: string | ((validation: ValidationResult) => string);
  autoComplete?: string;
  inputMode?: "text" | "numeric" | "decimal" | "email" | "url" | "search" | "tel";
}

export function TextField({ label, description, placeholder, errorMessage, autoComplete, inputMode, className, ...props }: TextFieldProps) {
  return (
    <AriaTextField {...props} className={["cf-field", typeof className === "string" ? className : ""].join(" ").trim()}>
      <Label className="cf-field__label">{label}</Label>
      <Input className="cf-input" {...(placeholder ? { placeholder } : {})} {...(autoComplete ? { autoComplete } : {})} {...(inputMode ? { inputMode } : {})} />
      {description && (
        <Text slot="description" className="cf-field__description">
          {description}
        </Text>
      )}
      <FieldError className="cf-field__error">{errorMessage}</FieldError>
    </AriaTextField>
  );
}
