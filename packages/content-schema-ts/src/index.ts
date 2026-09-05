// Compiled Ajv (draft 2020-12) validators for every exported contract.
// The schema JSON files are the artifact exported by `scripts/export_schemas.py`.
import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020.js";
import addFormats from "ajv-formats";

import { SCHEMA_NAMES, type SchemaName } from "../generated/index.js";
import AlignmentReport from "../schema/AlignmentReport.schema.json";
import AnimationSpec from "../schema/AnimationSpec.schema.json";
import ArtboardSpec from "../schema/ArtboardSpec.schema.json";
import AudioArtifactReport from "../schema/AudioArtifactReport.schema.json";
import AudioMixSpec from "../schema/AudioMixSpec.schema.json";
import CaptionTrack from "../schema/CaptionTrack.schema.json";
import ClaimRecord from "../schema/ClaimRecord.schema.json";
import ComfyProvenance from "../schema/ComfyProvenance.schema.json";
import CueSheet from "../schema/CueSheet.schema.json";
import ComputeOffer from "../schema/ComputeOffer.schema.json";
import ComfyWorkflowPackage from "../schema/ComfyWorkflowPackage.schema.json";
import CompiledTimeline from "../schema/CompiledTimeline.schema.json";
import ContentCampaign from "../schema/ContentCampaign.schema.json";
import ContentDeliverable from "../schema/ContentDeliverable.schema.json";
import ControlAsset from "../schema/ControlAsset.schema.json";
import ControlBundle from "../schema/ControlBundle.schema.json";
import DatasetTable from "../schema/DatasetTable.schema.json";
import DeliverableDAG from "../schema/DeliverableDAG.schema.json";
import DeliveryPackage from "../schema/DeliveryPackage.schema.json";
import DependencyImpact from "../schema/DependencyImpact.schema.json";
import DestinationBinding from "../schema/DestinationBinding.schema.json";
import EditorialStyleKit from "../schema/EditorialStyleKit.schema.json";
import EnergyReport from "../schema/EnergyReport.schema.json";
import EditBatch from "../schema/EditBatch.schema.json";
import EditOperation from "../schema/EditOperation.schema.json";
import EpisodeMetadata from "../schema/EpisodeMetadata.schema.json";
import EpisodeOutline from "../schema/EpisodeOutline.schema.json";
import EvidenceRecord from "../schema/EvidenceRecord.schema.json";
import EvidenceRequirementPlan from "../schema/EvidenceRequirementPlan.schema.json";
import ExecutionDecision from "../schema/ExecutionDecision.schema.json";
import ExecutionPolicy from "../schema/ExecutionPolicy.schema.json";
import FixPlan from "../schema/FixPlan.schema.json";
import FrameSpec from "../schema/FrameSpec.schema.json";
import GenerationLock from "../schema/GenerationLock.schema.json";
import HardwareInventory from "../schema/HardwareInventory.schema.json";
import LayerSpec from "../schema/LayerSpec.schema.json";
import LoudnessReport from "../schema/LoudnessReport.schema.json";
import MasterChainSpec from "../schema/MasterChainSpec.schema.json";
import ModelDescriptor from "../schema/ModelDescriptor.schema.json";
import MotionPlan from "../schema/MotionPlan.schema.json";
import MusicTrack from "../schema/MusicTrack.schema.json";
import NarrationRequest from "../schema/NarrationRequest.schema.json";
import NarrationSegment from "../schema/NarrationSegment.schema.json";
import NodeCapabilityReport from "../schema/NodeCapabilityReport.schema.json";
import PlacementDecision from "../schema/PlacementDecision.schema.json";
import ProjectBrief from "../schema/ProjectBrief.schema.json";
import RenderBundle from "../schema/RenderBundle.schema.json";
import ResearchPack from "../schema/ResearchPack.schema.json";
import RevisionOutcome from "../schema/RevisionOutcome.schema.json";
import RevisionRequest from "../schema/RevisionRequest.schema.json";
import SceneSpec from "../schema/SceneSpec.schema.json";
import ShortsPlan from "../schema/ShortsPlan.schema.json";
import ShotPlan from "../schema/ShotPlan.schema.json";
import ReferenceClip from "../schema/ReferenceClip.schema.json";
import ReferenceLibrary from "../schema/ReferenceLibrary.schema.json";
import ReferenceMatchSet from "../schema/ReferenceMatchSet.schema.json";
import ReferenceQuery from "../schema/ReferenceQuery.schema.json";
import ShotRouting from "../schema/ShotRouting.schema.json";
import WorkflowTemplate from "../schema/WorkflowTemplate.schema.json";
import ShotSpec from "../schema/ShotSpec.schema.json";
import SkillManifest from "../schema/SkillManifest.schema.json";
import SoundConditionReport from "../schema/SoundConditionReport.schema.json";
import SoundConditionSpec from "../schema/SoundConditionSpec.schema.json";
import SourceRecord from "../schema/SourceRecord.schema.json";
import SpeechRestorationReport from "../schema/SpeechRestorationReport.schema.json";
import SpeechRestorationSpec from "../schema/SpeechRestorationSpec.schema.json";
import StoragePlan from "../schema/StoragePlan.schema.json";
import StoryPlan from "../schema/StoryPlan.schema.json";
import WorkspaceGraph from "../schema/WorkspaceGraph.schema.json";

export type * from "../generated/index.js";
export { SCHEMA_NAMES };

const SCHEMAS: Record<SchemaName, object> = {
  AlignmentReport,
  AnimationSpec,
  ArtboardSpec,
  AudioArtifactReport,
  AudioMixSpec,
  CaptionTrack,
  ClaimRecord,
  ComfyProvenance,
  ComputeOffer,
  ComfyWorkflowPackage,
  CompiledTimeline,
  ContentCampaign,
  ContentDeliverable,
  ControlAsset,
  ControlBundle,
  DatasetTable,
  DeliverableDAG,
  DeliveryPackage,
  DependencyImpact,
  DestinationBinding,
  EditorialStyleKit,
  EnergyReport,
  CueSheet,
  EditBatch,
  EditOperation,
  EpisodeMetadata,
  EpisodeOutline,
  EvidenceRecord,
  EvidenceRequirementPlan,
  ExecutionDecision,
  ExecutionPolicy,
  FixPlan,
  FrameSpec,
  GenerationLock,
  HardwareInventory,
  LayerSpec,
  LoudnessReport,
  MasterChainSpec,
  ModelDescriptor,
  MotionPlan,
  MusicTrack,
  NarrationRequest,
  NarrationSegment,
  NodeCapabilityReport,
  PlacementDecision,
  ProjectBrief,
  RenderBundle,
  ResearchPack,
  RevisionOutcome,
  RevisionRequest,
  SceneSpec,
  ShortsPlan,
  ShotPlan,
  ReferenceClip,
  ReferenceLibrary,
  ReferenceMatchSet,
  ReferenceQuery,
  ShotRouting,
  WorkflowTemplate,
  ShotSpec,
  SkillManifest,
  SoundConditionReport,
  SoundConditionSpec,
  SourceRecord,
  SpeechRestorationReport,
  SpeechRestorationSpec,
  StoragePlan,
  StoryPlan,
  WorkspaceGraph,
};

const ajv = new Ajv2020({
  strict: true,
  allErrors: true,
  discriminator: true,
  allowUnionTypes: true,
});
addFormats(ajv);
for (const name of SCHEMA_NAMES) ajv.addSchema(SCHEMAS[name]);

export interface ValidationResult {
  ok: boolean;
  errors: ErrorObject[];
}

export function validatorFor(name: SchemaName): ValidateFunction {
  const id = (SCHEMAS[name] as { $id: string }).$id;
  const fn = ajv.getSchema(id);
  if (!fn) throw new Error(`No compiled validator for ${name}`);
  return fn;
}

export function validate(name: SchemaName, value: unknown): ValidationResult {
  const fn = validatorFor(name);
  const ok = fn(value) as boolean;
  return { ok, errors: ok ? [] : [...(fn.errors ?? [])] };
}

export function assertValid<T>(name: SchemaName, value: unknown): T {
  const result = validate(name, value);
  if (!result.ok) {
    throw new Error(`${name} failed validation: ${ajv.errorsText(result.errors)}`);
  }
  return value as T;
}
