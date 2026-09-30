import type { TemplateFieldSchema } from '../types/field';
import type { Template, TemplateDetail } from '../types/template';
import { api } from './client';

export const templatesApi = {
  list: () => api.get<Template[]>('/api/templates'),

  get: (id: string) => api.get<TemplateDetail>(`/api/templates/${id}`),

  fields: (id: string) => api.get<TemplateFieldSchema>(`/api/templates/${id}/fields`),

  upload: (file: File, name: string, description?: string) => {
    const form = new FormData();
    form.append('file', file);
    form.append('name', name);
    if (description) form.append('description', description);
    return api.upload<{ template_id: string; task_id: string | null; status: string }>(
      '/api/templates',
      form,
    );
  },

  updateField: (
    templateId: string,
    fieldId: string,
    updates: Record<string, unknown>,
  ) => api.put<{ field_id: string; updated: string[] }>(
    `/api/templates/${templateId}/fields/${encodeURIComponent(fieldId)}`,
    updates,
  ),

  reanalyze: (id: string) =>
    api.post<{ template_id: string; status: string }>(`/api/templates/${id}/reanalyze`),

  archive: (id: string) => api.del<{ status: string }>(`/api/templates/${id}`),

  /**
   * Admin only. Deletes the template for good; refused with 409
   * (`details.contract_count`) while contracts still reference it.
   */
  purge: (id: string) =>
    api.del<{ id: string; deleted: boolean }>(`/api/templates/${id}?purge=true`),
};
