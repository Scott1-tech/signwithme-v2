export type FieldType =
  | 'text_line'
  | 'checkbox'
  | 'checkbox_group'
  | 'signature'
  | 'date'
  | 'table';

/**
 * Canonical bounding box, matching the backend exactly: PDF points, origin at
 * the TOP-LEFT of the page. The renderer multiplies by the viewer's scale to
 * get CSS pixels; nothing else converts.
 */
export interface BBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface CheckboxOption {
  id: string;
  label: string;
  bbox: BBox;
  region_bbox?: BBox | null;
}

export interface FieldRegion {
  field_id: string;
  label: string;
  field_type: FieldType;
  page: number;
  bbox: BBox;
  required: boolean;
  options?: CheckboxOption[] | null;
  validation_rule?: string | null;
  /** The date field printed alongside this signature, resolved at detection
   *  time so placement never guesses from a pixel offset. */
  paired_date_field_id?: string | null;
  detector?: string | null;
}

export interface PageSchema {
  page_number: number;
  width: number;
  height: number;
  rotation: number;
  fields: FieldRegion[];
}

export interface TemplateFieldSchema {
  pages: PageSchema[];
  field_count: number;
}
