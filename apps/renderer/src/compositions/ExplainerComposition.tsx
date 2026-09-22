import { ExplainerComposition as Explainer, calculateExplainerMetadata, type ExplainerCompositionProps } from "@content-factory/explainer-ui";

import "../fonts";

export { calculateExplainerMetadata, type ExplainerCompositionProps };

export const ExplainerComposition: React.FC<ExplainerCompositionProps> = ({ bundle }) => <Explainer bundle={bundle} />;
