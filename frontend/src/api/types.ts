// Short names for the generated API types. schema.ts is generated from the backend's OpenAPI
// schema by `npm run gen:api`: never edit it by hand, regenerate it.
import type { components, paths } from './schema'

export type Schemas = components['schemas']

/** Any investigation event: an item of `GET /investigations/{id}/events`, and the `data` of
 * each `/stream` frame. Narrow it on `type`; each type has its own `payload`. */
export type InvestigationEvent =
  paths['/investigations/{investigation_id}/events']['get']['responses'][200]['content']['application/json'][number]

export type EventType = InvestigationEvent['type']

/** One event type, e.g. `EventOf<'evidence_added'>`. */
export type EventOf<T extends EventType> = Extract<InvestigationEvent, { type: T }>

export type Investigation = Schemas['InvestigationOut']
export type InvestigationStatus = Schemas['InvestigationStatus']
export type Report = Schemas['InvestigationReport']
export type Step = Schemas['Step']
export type ModelTier = Schemas['ModelTier']
