// @content-factory/explainer-ui — tokens, colour math, the state fold, the templates and ExplainerComposition.
export * from "./tokens.gen";
export * from "./oklch";
export * from "./apca";
export * from "./palette";
export * from "./geometry";
export * from "./text";
export * from "./state";
export * from "./source";
export * from "./context";
export * from "./capabilities";
export * from "./templates/chart";
export type { TemplateProps } from "./templates/props";
export { ChartTemplate } from "./templates/ChartTemplate";
export { DiagramTemplate } from "./templates/DiagramTemplate";
export { SourceDocumentTemplate } from "./templates/SourceDocumentTemplate";
export { TextTemplate } from "./templates/TextTemplate";
export { ExplainerComposition } from "./ExplainerComposition";
