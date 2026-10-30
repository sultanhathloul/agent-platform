// Thin re-exports of generated API types (README §6.3). Never hand-write API
// types here; regenerate with `npm run gen:api` when the specs change.
import type { components } from "./gateway";

export type {
  AgentEvent as StreamEvent,
  ArtifactRef,
  Citation,
  DoneData,
  ErrorData,
  GuardData,
  TierData,
  ToolEndData,
  ToolStartData,
  Usage,
} from "./events";

type Schemas = components["schemas"];

export type ChatTurnRequest = Schemas["ChatTurnRequest"];
export type ApiError = Schemas["ApiError"];
export type ApiErrorEnvelope = Schemas["ApiErrorEnvelope"];
export type MemoryList = Schemas["MemoryList"];
export type MemoryItem = Schemas["MemoryItem"];
export type MemorySettings = Schemas["MemorySettings"];
export type MeResponse = Schemas["MeResponse"];
export type PolicyDocument = Schemas["PolicyDocument"];
export type PolicyView = Schemas["PolicyView"];
export type PolicyVersionView = Schemas["PolicyVersionView"];
export type PolicyList = Schemas["PolicyList"];
export type PolicyDetail = Schemas["PolicyDetail"];
export type PolicyTestRequest = Schemas["PolicyTestRequest"];
export type PolicyTestResult = Schemas["PolicyTestResult"];
export type PolicySpan = Schemas["PolicySpan"];
export type PolicyYaml = Schemas["PolicyYaml"];
export type PolicyImportReport = Schemas["PolicyImportReport"];
export type PolicyMetrics = Schemas["PolicyMetrics"];
export type PolicyMatchRow = Schemas["PolicyMatchRow"];
export type PolicyOverrides = Schemas["PolicyOverrides"];
export type PolicyRoles = Schemas["PolicyRoles"];
export type PolicyChanges = Schemas["PolicyChanges"];
export type PolicyChange = Schemas["PolicyChange"];
export type SimulatedPrincipal = Schemas["SimulatedPrincipal"];
export type PolicyWarning = Schemas["PolicyWarning"];
export type AttachmentView = Schemas["AttachmentView"];
export type AttachmentList = Schemas["AttachmentList"];
export type AttachmentLimits = Schemas["AttachmentLimits"];
export type ConversationList = Schemas["ConversationList"];
export type ConversationSummary = Schemas["ConversationSummary"];
export type ConversationMessages = Schemas["ConversationMessages"];
export type HistoryMessage = Schemas["HistoryMessage"];
export type HistorySkill = Schemas["HistorySkill"];
export type SkillList = Schemas["SkillList"];
export type SkillView = Schemas["SkillView"];
export type PlatformSettingsView = Schemas["PlatformSettingsView"];
export type SettingField = Schemas["SettingField"];
export type ReadOnlySetting = Schemas["ReadOnlySetting"];
export type TimeoutPair = Schemas["TimeoutPair"];
export type SettingsProblem = Schemas["SettingsProblem"];
export type SettingsDraft = Schemas["SettingsDraft"];
export type SettingsRevert = Schemas["SettingsRevert"];
export type SettingsPreview = Schemas["SettingsPreview"];
export type SettingChange = Schemas["SettingChange"];
export type SettingsSaved = Schemas["SettingsSaved"];
export type SettingsVersionView = Schemas["SettingsVersionView"];
export type SettingsVersions = Schemas["SettingsVersions"];
export type PlatformUsage = Schemas["PlatformUsage"];
export type TurnsUsage = Schemas["TurnsUsage"];
export type TenantUsage = Schemas["TenantUsage"];
export type ServerUsage = Schemas["ServerUsage"];
export type CapabilityList = Schemas["CapabilityList"];
export type CapabilityView = Schemas["CapabilityView"];
export type KbToggle = Schemas["KbToggle"];
export type KbView = Schemas["KbView"];
export type KbList = Schemas["KbList"];
export type KbCreate = Schemas["KbCreate"];
export type KbDeleted = Schemas["KbDeleted"];
export type KbDocumentView = Schemas["KbDocumentView"];
export type KbDocumentList = Schemas["KbDocumentList"];
export type KbSourceMetadata = Schemas["SourceMetadata"];

export type ReportOutput = NonNullable<ChatTurnRequest["output"]>;
export type HistoryArtifact = Schemas["HistoryArtifact"];
export type ReportView = Schemas["ReportView"];
export type ReportList = Schemas["ReportList"];
export type ReportVersionSummary = Schemas["ReportVersionInfo"];
export type ReportVersionView = Schemas["ReportVersionView"];
export type ReportViewLink = Schemas["ViewLink"];
