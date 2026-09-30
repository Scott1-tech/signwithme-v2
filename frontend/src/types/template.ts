import type { PrefilledSummary } from '../lib/warnings';
import type { TemplateFieldSchema } from './field';

export type TemplateStatus = 'processing' | 'ready' | 'failed' | 'archived';

export interface Template {
  id: string;
  name: string;
  description: string | null;
  page_count: number;
  status: TemplateStatus;
  error_message: string | null;
  /** Additive: absent/null on older servers. Read through `readPrefilledWarning`. */
  prefilled_summary?: PrefilledSummary | null;
  created_at: string;
  updated_at: string;
}

export interface TemplateDetail extends Template {
  field_schema: TemplateFieldSchema;
}

export interface User {
  id: string;
  email: string;
  full_name: string | null;
  role: 'admin' | 'reviewer' | 'contractor';
  is_active: boolean;
}
