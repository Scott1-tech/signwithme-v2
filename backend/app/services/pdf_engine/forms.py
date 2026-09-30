"""Interactive form fields (EDITOR-V2-PLAN §3 `field.*`, §4 `forms.py`; B3).

Six field types, one widget each (a radio GROUP = one field, one widget per
button):

    text, multiline   /FT /Tx          (multiline: Ff bit 13)
    dropdown          /FT /Ch combo     /Opt
    checkbox          /FT /Btn          "on" state = `export_value` (default Yes)
    radio             /FT /Btn radio    parent field (/T, /V, /Ff, /Kids) +
                                        one kid widget per button, "on" state
                                        = the button's `export_value`
    signature         /FT /Sig          empty signature box

Field-level keys (`/T /FT /Ff /V /DV /TU /MaxLen /Opt /SWMFont`) live on the
TERMINAL field dictionary: the widget itself, or -- radio groups, and
hierarchical fields of an uploaded PDF -- its `/Parent`. Widget-level keys:
`/Rect /AS /AP /SWMTab`.

Radio buttons are restructured at the dictionary level (spike [S5]): PyMuPDF
makes every button its own field with a STRING `/V`, and `field_value=True`
fails with "bad xref". Here the group `/V` and every kid's `/AS` are PDF names,
and the kids' appearance "on" states are renamed to their export values, so
pypdf and pdf.js read the group as one field with the right value.

Private keys (ignored by every viewer): `/SWMTab n` on a widget (its
`tab_index`) and `/SWMFont /Family` on the field (the family asked for).
Widgets are drawn with the metric-compatible standard-14 face
(`FACE_FOR_FAMILY`): an AcroForm appearance cannot use an embedded subset the
way page text does, and every PDF viewer has these faces. Values are
therefore limited to WinAnsi (cp1252) characters (422 `undrawable_character`).

Flatten (`flatten`) = `bake`: every widget's appearance becomes page content,
the widgets and the AcroForm go. Used by the final (always) and by an export
with `flatten_forms`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.pdf_engine import adapter, errors
from app.services.pdf_engine.model import Rect, rect_dict

# Field flags (/Ff), PDF 32000-1 §12.7.3.1, §12.7.4.
FF_READONLY = 1 << 0
FF_REQUIRED = 1 << 1
FF_MULTILINE = 1 << 12
FF_NO_TOGGLE_TO_OFF = 1 << 14
FF_RADIO = 1 << 15
FF_PUSHBUTTON = 1 << 16
FF_COMBO = 1 << 17
# Annotation flags (/F): Hidden, NoView.
F_HIDDEN = 1 << 1
F_NOVIEW = 1 << 5

FACE_FOR_FAMILY = {
    "LiberationSans": "Helv",
    "Arimo": "Helv",
    "Carlito": "Helv",
    "DejaVuSans": "Helv",
    "LiberationSerif": "TiRo",
    "Caladea": "TiRo",
    "DejaVuSerif": "TiRo",
    "Amiri": "TiRo",
    "LiberationMono": "Cour",
}
FAMILY_FOR_FACE = {"Helv": "LiberationSans", "TiRo": "LiberationSerif", "Cour": "LiberationMono"}
DEFAULT_FACE = "Helv"
_BASE_FONTS = {"Helv": "Helvetica", "TiRo": "Times-Roman", "Cour": "Courier", "ZaDb": "ZapfDingbats"}

SWM_TAB = "SWMTab"
SWM_FONT = "SWMFont"
OFF = "Off"

_REF = re.compile(r"(\d+)\s+0\s+R")


# -- engine-side ops ----------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    rect: Rect
    required: bool = False
    readonly: bool = False
    tooltip: str | None = None
    default: object = None
    options: tuple[str, ...] | None = None
    export_value: str | None = None
    font: str | None = None
    size_pt: float | None = None
    max_len: int | None = None
    tab_index: int = 0


@dataclass(frozen=True)
class FieldOp:
    id: str
    kind: str  # field.add | field.update | field.remove | field.value
    page_uid: str | None = None
    name: str | None = None  # remove/value: the field; update: its current name
    export_value: str | None = None  # update/remove: which radio button
    spec: FieldSpec | None = None
    value: object = None


# -- reading ----------------------------------------------------------------------


@dataclass
class Widget:
    """One widget as read from the file, with its field's properties."""

    index: int
    xref: int
    field_xref: int  # the terminal field dictionary (== xref when merged)
    parent: int | None
    name: str
    type: str  # text multiline dropdown radio checkbox signature other
    rect: Rect
    required: bool
    readonly: bool
    tooltip: str | None
    default: object
    options: list[str] | None
    export_value: str | None
    font: str | None
    size_pt: float | None
    max_len: int | None
    tab_index: int | None
    value: object
    source: str  # editor | original
    hidden: bool
    raw_kind: str = ""


def _name_value(kind: str, value: str) -> str | None:
    """`/V` or `/AS` of a button as a plain state name (None = unset)."""
    if kind == "name":
        return value[1:] if value.startswith("/") else value
    if kind == "string":  # PyMuPDF writes a radio's /V as a string (spike [S5])
        return value
    return None


def _terminal_field(doc: adapter.Doc, xref: int) -> tuple[int, int | None]:
    """(terminal field xref, parent xref or None) of a widget."""
    kind, value = doc.get_key(xref, "Parent")
    if kind != "xref":
        return xref, None
    parent = int(value.split()[0])
    has_own_name = doc.get_key(xref, "T")[0] != "null"
    return (xref if has_own_name else parent), parent


def read_widgets(doc: adapter.Doc, indices=None) -> list[Widget]:
    out: list[Widget] = []
    for index in (range(doc.page_count) if indices is None else indices):
        for raw in doc.widgets(index):
            out.append(_widget(doc, index, raw))
    return out


def _widget(doc: adapter.Doc, index: int, raw: dict) -> Widget:
    xref = raw["xref"]
    field_xref, parent = _terminal_field(doc, xref)
    flags = raw["field_flags"]
    kind = raw["kind"]
    if kind == "text":
        ftype = "multiline" if flags & FF_MULTILINE else "text"
    elif kind == "combobox":
        ftype = "dropdown"
    elif kind in ("checkbox", "radio", "signature"):
        ftype = kind
    else:
        ftype = "other"
    tab_kind, tab_value = doc.get_key(xref, SWM_TAB)
    tab_index = int(float(tab_value)) if tab_kind in ("int", "float") else None
    font_kind, font_value = doc.get_key(field_xref, SWM_FONT)
    family = font_value[1:] if font_kind == "name" else FAMILY_FOR_FACE.get(raw["font"] or "")
    export_value = raw["on_states"][0] if ftype in ("checkbox", "radio") and raw["on_states"] else None
    value: object
    if ftype in ("checkbox", "radio"):
        state = _name_value(*doc.get_key(field_xref, "V"))
        if ftype == "checkbox":
            value = bool(state) and state != OFF
        else:
            value = "" if not state or state == OFF else state
        dv = _name_value(*doc.get_key(field_xref, "DV"))
        default = bool(dv) and dv != OFF and (ftype == "checkbox" or dv == export_value)
    elif ftype == "signature":
        value, default = None, None
    else:
        current = raw["value"]
        if isinstance(current, (list, tuple)):
            value = [str(v) for v in current]
        else:
            value = "" if current in (None, OFF) and ftype != "text" else str(current or "")
        dv_kind, dv_value = doc.get_key(field_xref, "DV")
        default = dv_value if dv_kind == "string" else None
    rect = raw["rect"]
    hidden = bool(raw["annot_flags"] & (F_HIDDEN | F_NOVIEW)) or (rect[2] - rect[0]) <= 0.01 or (
        rect[3] - rect[1]) <= 0.01
    return Widget(
        index=index,
        xref=xref,
        field_xref=field_xref,
        parent=parent,
        name=raw["name"],
        type=ftype,
        rect=rect,
        required=bool(flags & FF_REQUIRED),
        readonly=bool(flags & FF_READONLY),
        tooltip=raw["tooltip"],
        default=default,
        options=raw["options"] if ftype == "dropdown" else None,
        export_value=export_value,
        font=family if ftype in ("text", "multiline", "dropdown") else None,
        size_pt=(raw["size"] or None) if ftype in ("text", "multiline", "dropdown") else None,
        max_len=(raw["max_len"] or None) if ftype in ("text", "multiline") else None,
        tab_index=tab_index,
        value=value,
        source="editor" if tab_index is not None else "original",
        hidden=hidden,
        raw_kind=kind,
    )


def field_dicts(pdf_bytes: bytes, uid_by_index: dict[int, str]) -> list[dict]:
    """`FieldOut` rows of a version, in tab order (editor fields by
    `tab_index`, then the originals by page and position)."""
    with adapter.Doc(pdf_bytes) as doc:
        widgets = read_widgets(doc)
    order = {id(w): n for n, w in enumerate(widgets)}
    widgets.sort(key=lambda w: (w.tab_index is None, w.tab_index or 0, w.index, order[id(w)]))
    return [
        {
            "name": w.name,
            "type": w.type,
            "page_uid": uid_by_index.get(w.index, ""),
            "rect": rect_dict(w.rect),
            "required": w.required,
            "readonly": w.readonly,
            "tooltip": w.tooltip,
            "default": w.default,
            "options": w.options,
            "export_value": w.export_value,
            "font": w.font,
            "size_pt": w.size_pt,
            "max_len": w.max_len,
            "tab_index": w.tab_index,
            "value": w.value,
            "source": w.source,
            "hidden": w.hidden,
        }
        for w in widgets
    ]


def widget_rects(pdf_bytes: bytes, index: int) -> list[Rect]:
    with adapter.Doc(pdf_bytes) as doc:
        return [raw["rect"] for raw in doc.widgets(index)]


# -- dictionary helpers --------------------------------------------------------------


def _refs(doc: adapter.Doc, xref: int, key: str) -> tuple[list[int], int | None]:
    """The references of an array entry, and the xref of the array when it
    is an indirect object (else None)."""
    kind, value = doc.get_key(xref, key)
    if kind == "xref":
        holder = int(value.split()[0])
        return [int(n) for n in _REF.findall(doc.object_source(holder))], holder
    if kind == "array":
        return [int(n) for n in _REF.findall(value)], None
    return [], None


def _set_refs(doc: adapter.Doc, xref: int, key: str, refs: list[int], holder: int | None) -> None:
    array = "[" + " ".join(f"{r} 0 R" for r in refs) + "]"
    if holder is not None:
        doc.replace_object(holder, array)
    else:
        doc.set_key(xref, key, array)


def _acroform_fields(doc: adapter.Doc) -> tuple[list[int], int | None]:
    return _refs(doc, doc.catalog_xref(), "AcroForm/Fields")


def _set_acroform_fields(doc: adapter.Doc, refs: list[int], holder: int | None) -> None:
    _set_refs(doc, doc.catalog_xref(), "AcroForm/Fields", refs, holder)


def _ensure_acroform_resources(doc: adapter.Doc) -> None:
    """`/DR` with the four standard faces the widgets name, and a `/DA`, so a
    viewer that regenerates an appearance finds its font."""
    catalog = doc.catalog_xref()
    for face, base in _BASE_FONTS.items():
        if doc.get_key(catalog, f"AcroForm/DR/Font/{face}")[0] == "null":
            encoding = "" if face == "ZaDb" else "/Encoding/WinAnsiEncoding"
            font = doc.new_object(f"<</Type/Font/Subtype/Type1/BaseFont/{base}{encoding}>>")
            doc.set_key(catalog, f"AcroForm/DR/Font/{face}", f"{font} 0 R")
    if doc.get_key(catalog, "AcroForm/DA")[0] == "null":
        doc.set_key(catalog, "AcroForm/DA", doc.pdf_string("/Helv 0 Tf 0 g"))


def _detach(doc: adapter.Doc, node: int, parent: int | None) -> None:
    """Take field/widget `node` out of the field tree; a parent left without
    kids goes too."""
    if parent is None:
        refs, holder = _acroform_fields(doc)
        if node in refs:
            _set_acroform_fields(doc, [r for r in refs if r != node], holder)
        return
    kids, holder = _refs(doc, parent, "Kids")
    kids = [k for k in kids if k != node]
    if kids:
        _set_refs(doc, parent, "Kids", kids, holder)
        return
    kind, value = doc.get_key(parent, "Parent")
    _detach(doc, parent, int(value.split()[0]) if kind == "xref" else None)


def _remove_widget(doc: adapter.Doc, widget: Widget) -> None:
    if widget.type == "radio" and widget.value and widget.value == widget.export_value:
        # The chosen button goes: the group is left with no choice.
        doc.set_key(widget.field_xref, "V", "/" + OFF)
    page = doc.page_xref(widget.index)
    annots, holder = _refs(doc, page, "Annots")
    _set_refs(doc, page, "Annots", [a for a in annots if a != widget.xref], holder)
    _detach(doc, widget.xref, widget.parent)
    doc.reload_page(widget.index)


def _rename_on_state(doc: adapter.Doc, xref: int, new: str) -> None:
    """Rename the appearance "on" state (whatever PyMuPDF called it) to `new`."""
    for key in ("AP/N", "AP/D"):
        kind, value = doc.get_key(xref, key)
        if kind != "dict":
            continue
        names = [n for n in re.findall(r"/([A-Za-z0-9_.\-#]+)\s+\d+\s+0\s+R", value) if n != OFF]
        for old in names:
            if old != new:
                value = re.sub(rf"/{re.escape(old)}(\s+\d+\s+0\s+R)", rf"/{new}\1", value)
        doc.set_key(xref, key, value)


def _flags(spec: FieldSpec) -> int:
    flags = (FF_READONLY if spec.readonly else 0) | (FF_REQUIRED if spec.required else 0)
    if spec.type == "multiline":
        flags |= FF_MULTILINE
    elif spec.type == "dropdown":
        flags |= FF_COMBO
    elif spec.type == "radio":
        flags |= FF_RADIO | FF_NO_TOGGLE_TO_OFF
    return flags


def _face(spec: FieldSpec) -> str:
    return FACE_FOR_FAMILY.get(spec.font or "", DEFAULT_FACE)


# -- the form as the ops see it -----------------------------------------------------


class Form:
    """The fields of a document being edited: read from the file, kept up to
    date by every op this module carries out."""

    def __init__(self, doc: adapter.Doc, uid_by_index: dict[int, str]):
        self.doc = doc
        self.uid_by_index = uid_by_index
        self.widgets: list[Widget] = read_widgets(doc)

    def refresh(self) -> None:
        self.widgets = read_widgets(self.doc)

    def of(self, name: str) -> list[Widget]:
        return [w for w in self.widgets if w.name == name]

    def index_of(self, page_uid: str) -> int | None:
        for index, uid in self.uid_by_index.items():
            if uid == page_uid:
                return index
        return None


def _fail(code: str, message: str, op_id: str, **details) -> errors.EngineError:
    return errors.EngineError(code, message, {"op_id": op_id, **details})


def _check_rect(doc: adapter.Doc, index: int, rect: Rect, op_id: str) -> None:
    width, height = doc.page_size(index)
    x0, y0, x1, y1 = rect
    if x0 < -0.5 or y0 < -0.5 or x1 > width + 0.5 or y1 > height + 0.5:
        raise _fail(errors.OP_OUTSIDE_PAGE, "The field reaches outside the page", op_id)


# -- creating ------------------------------------------------------------------------


def _create(form: Form, op_id: str, index: int, spec: FieldSpec, value=None) -> None:
    """A new field (or a new button of an existing radio group)."""
    doc = form.doc
    existing = form.of(spec.name)
    if spec.type == "radio":
        if existing and not all(w.type == "radio" for w in existing):
            raise _fail(errors.FIELD_NAME_TAKEN, "A field with that name exists", op_id)
        if any(w.export_value == spec.export_value for w in existing):
            raise _fail(errors.RADIO_VALUE_TAKEN, "That radio group already has a button with this value", op_id)
        if existing and not all(w.parent is not None and w.field_xref == w.parent for w in existing):
            # Same-named radios of an uploaded PDF that are separate fields
            # (not kids of one group): nothing to join.
            raise _fail(errors.FIELD_NAME_TAKEN, "A field with that name exists", op_id)
        _create_radio_button(form, index, spec, existing, value)
    else:
        if existing:
            raise _fail(errors.FIELD_NAME_TAKEN, "A field with that name exists", op_id)
        _create_single(form, index, spec, value)
    _ensure_acroform_resources(doc)
    form.refresh()


def _text_value(spec: FieldSpec, value) -> str:
    if isinstance(value, str):
        return value
    return spec.default if isinstance(spec.default, str) else ""


def _signature_appearance(doc: adapter.Doc, xref: int, rect: Rect) -> None:
    """An EMPTY signature box looks like one: a thin grey frame (PyMuPDF's
    default is an orange "SIGN" tag, which would end up in a flattened final)."""
    width, height = rect[2] - rect[0], rect[3] - rect[1]
    stream = f"q 0.6 G 0.75 w 0.375 0.375 {width - 0.75:.3f} {height - 0.75:.3f} re S Q\n".encode()
    ap = doc.new_stream(f"<</Type/XObject/Subtype/Form/BBox[0 0 {width:.3f} {height:.3f}]/Resources<<>>>>", stream)
    doc.set_key(xref, "AP", f"<</N {ap} 0 R>>")


def _create_single(form: Form, index: int, spec: FieldSpec, value) -> None:
    doc = form.doc
    sig_flags = doc.get_key(doc.catalog_xref(), "AcroForm/SigFlags") if spec.type == "signature" else None
    kind = {"text": "text", "multiline": "text", "dropdown": "combobox"}.get(spec.type, spec.type)
    initial = None
    if spec.type in ("text", "multiline", "dropdown"):
        initial = _text_value(spec, value)
    xref = doc.add_widget(
        index,
        kind=kind,
        name=spec.name,
        rect=spec.rect,
        field_flags=_flags(spec),
        value=initial,
        options=list(spec.options or []),
        font=_face(spec),
        size=float(spec.size_pt or 0.0),
        max_len=int(spec.max_len or 0),
        tooltip=spec.tooltip,
    )
    doc.set_key(xref, SWM_TAB, str(int(spec.tab_index)))
    if spec.type in ("text", "multiline", "dropdown"):
        doc.set_key(xref, SWM_FONT, "/" + (spec.font or FAMILY_FOR_FACE[_face(spec)]))
        if isinstance(spec.default, str) and spec.default:
            doc.set_key(xref, "DV", doc.pdf_string(spec.default))
    if spec.type == "checkbox":
        on = spec.export_value or "Yes"
        _rename_on_state(doc, xref, on)
        checked = value if isinstance(value, bool) else bool(spec.default)
        state = on if checked else OFF
        doc.set_key(xref, "V", "/" + state)
        doc.set_key(xref, "AS", "/" + state)
        if spec.default:
            doc.set_key(xref, "DV", "/" + on)
    if spec.type == "signature":
        _signature_appearance(doc, xref, spec.rect)
        # PyMuPDF sets /SigFlags 3 (SignaturesExist + AppendOnly) for an EMPTY
        # field; that is only true once a field is really signed.
        doc.set_key(doc.catalog_xref(), "AcroForm/SigFlags", sig_flags[1] if sig_flags[0] == "int" else "0")
    doc.reload_page(index)


def _create_radio_button(form: Form, index: int, spec: FieldSpec, group: list[Widget], value) -> None:
    doc = form.doc
    # PyMuPDF writes the button with its appearance streams (Off + an "on"
    # state) as a field of its own; it is then turned into a KID of the group.
    xref = doc.add_widget(index, kind="radio", name=spec.name, rect=spec.rect, field_flags=_flags(spec))
    _rename_on_state(doc, xref, spec.export_value)
    parts = []
    for key in ("Type", "Subtype", "Rect", "F", "BS", "MK", "DA", "AP", "P"):
        kind, val = doc.get_key(xref, key)
        if kind == "null":
            continue
        parts.append(f"/{key}" + (doc.pdf_string(val) if kind == "string" else " " + val))
    # Out of the AcroForm /Fields (the group is the field).
    refs, holder = _acroform_fields(doc)
    _set_acroform_fields(doc, [r for r in refs if r != xref], holder)
    if group:
        parent = group[0].field_xref
        kids, kids_holder = _refs(doc, parent, "Kids")
        _set_refs(doc, parent, "Kids", kids + [xref], kids_holder)
    else:
        parent = doc.new_object(f"<</FT/Btn/T{doc.pdf_string(spec.name)}/Kids[{xref} 0 R]/V/{OFF}>>")
        refs, holder = _acroform_fields(doc)
        _set_acroform_fields(doc, refs + [parent], holder)
    parts.append(f"/Parent {parent} 0 R/AS/{OFF}/{SWM_TAB} {int(spec.tab_index)}")
    doc.replace_object(xref, "<<" + "".join(parts) + ">>")
    # Group-level properties follow the button last added / updated.
    doc.set_key(parent, "Ff", str(_flags(spec)))
    if spec.tooltip:
        doc.set_key(parent, "TU", doc.pdf_string(spec.tooltip))
    if spec.default:
        doc.set_key(parent, "DV", "/" + spec.export_value)
    selected = value if isinstance(value, str) else (spec.export_value if spec.default and not group else None)
    doc.reload_page(index)
    form.refresh()
    if selected:
        _set_radio(form, spec.name, selected)


# -- values --------------------------------------------------------------------------


def _set_button(form: Form, widgets: list[Widget], state: str) -> None:
    """Group/checkbox `/V` and every widget's `/AS` (names, not strings)."""
    doc = form.doc
    # One field dictionary for a proper group; an uploaded PDF may instead have
    # several same-named fields (PyMuPDF writes radios that way): all of them.
    for field_xref in dict.fromkeys(w.field_xref for w in widgets):
        doc.set_key(field_xref, "V", "/" + (state or OFF))
    for w in widgets:
        doc.set_key(w.xref, "AS", "/" + (state if state and w.export_value == state else OFF))
    for index in {w.index for w in widgets}:
        doc.reload_page(index)


def _set_radio(form: Form, name: str, value: str) -> None:
    group = form.of(name)
    _set_button(form, group, value)
    form.refresh()


def _winansi_problem(text: str) -> str | None:
    for char in text:
        if char == "\n":
            continue
        try:
            char.encode("cp1252")
        except UnicodeEncodeError:
            return f"U+{ord(char):04X}"
        if ord(char) < 0x20 or ord(char) == 0x7F:
            return f"U+{ord(char):04X}"
    return None


def check_value(widgets: list[Widget], value, op_id: str):
    """The value to write (normalised), or an EngineError. Never echoes it."""
    first = widgets[0]
    kind = first.type
    if kind == "signature":
        raise _fail(errors.FIELD_NOT_FILLABLE, "A signature field is signed, not filled", op_id)
    if kind == "other" and first.raw_kind != "listbox":
        raise _fail(errors.UNSUPPORTED_FIELD, "This kind of field cannot be filled here", op_id)
    if any(w.readonly for w in widgets):
        raise _fail(errors.FIELD_READONLY, "The field is read-only", op_id)
    invalid = lambda reason: _fail(errors.INVALID_FIELD_VALUE, "The value does not fit this field", op_id,  # noqa: E731
                                   reason=reason)
    if kind in ("text", "multiline"):
        if not isinstance(value, str):
            raise invalid("expected_text")
        value = value.replace("\r\n", "\n").replace("\r", "\n")
        if "\n" in value and kind == "text":
            raise invalid("newline_in_single_line")
        if first.max_len and len(value) > first.max_len:
            raise invalid("too_long")
        bad = _winansi_problem(value)
        if bad is not None:
            raise _fail(errors.UNDRAWABLE_CHARACTER, "A character cannot be drawn in a form field", op_id,
                        code_point=bad)
        return value
    if kind == "dropdown":
        if not isinstance(value, str):
            raise invalid("expected_option")
        if value and value not in (first.options or []):
            raise invalid("not_an_option")
        return value
    if first.raw_kind == "listbox":
        chosen = [value] if isinstance(value, str) else value
        if not isinstance(chosen, list) or any(v not in (first.options or []) for v in chosen):
            raise invalid("not_an_option")
        return chosen
    if kind == "checkbox":
        if not isinstance(value, bool):
            raise invalid("expected_boolean")
        return value
    if kind == "radio":
        if not isinstance(value, str):
            raise invalid("expected_export_value")
        if value and value not in {w.export_value for w in widgets}:
            raise invalid("not_a_button_of_the_group")
        return value
    raise invalid("unsupported")  # pragma: no cover


def _write_value(form: Form, widgets: list[Widget], value) -> None:
    first = widgets[0]
    if first.type == "checkbox":
        on = first.export_value or "Yes"
        _set_button(form, widgets, on if value else OFF)
    elif first.type == "radio":
        _set_button(form, widgets, value or OFF)
    else:
        for w in widgets:
            form.doc.set_widget_value(w.index, w.xref, value)
    form.refresh()


# -- the ops -------------------------------------------------------------------------


def _find(form: Form, name: str, export_value: str | None, op_id: str) -> list[Widget]:
    widgets = form.of(name)
    if export_value is not None:
        widgets = [w for w in widgets if w.export_value == export_value]
    if not widgets:
        raise _fail(errors.UNKNOWN_FIELD, "That form field is not in the document (any more)", op_id)
    return widgets


def _compatible(value, spec: FieldSpec):
    """The old value if it is still valid for the new definition, else None."""
    if value is None:
        return None
    if spec.type in ("text", "multiline") and isinstance(value, str):
        if spec.max_len and len(value) > spec.max_len:
            return None
        if spec.type == "text" and "\n" in value:
            return None
        return value
    if spec.type == "dropdown" and isinstance(value, str) and value in (spec.options or ()):
        return value
    if spec.type == "checkbox" and isinstance(value, bool):
        return value
    return None


def apply_field_ops(doc: adapter.Doc, final_uids: list[str], ops: list[FieldOp], *,
                    removed_names: set[str] | frozenset = frozenset(), deleted_uids=frozenset(),
                    warnings: list[dict], regions: list[dict], problems: list[dict] | None = None) -> bool:
    """Step 5 of `apply.apply_ops`: `field.*` in the order the draft holds
    them, then the tab order of every page they touched. `problems` given
    (draft validation): a refused op is recorded and skipped instead of
    raising. Returns True when anything was written."""
    if not ops:
        return False
    form = Form(doc, dict(enumerate(final_uids)))
    touched: set[int] = set()
    wrote = False
    for op in ops:
        try:
            done = _apply_one(form, op, removed_names, deleted_uids, warnings, regions, touched)
            wrote = wrote or done
        except errors.EngineError as exc:
            if problems is None:
                raise
            problems.append({"id": op.id, "reason": exc.code})
    for index in sorted(touched):
        _tab_order(doc, index)
    return wrote


def _apply_one(form: Form, op: FieldOp, removed_names, deleted_uids, warnings, regions, touched) -> bool:
    doc = form.doc
    if op.kind in ("field.add", "field.update"):
        if op.page_uid in deleted_uids:
            warnings.append({"code": "op_on_removed_page", "op_id": op.id, "page_uid": op.page_uid})
            return False
        index = form.index_of(op.page_uid)
        if index is None:
            raise _fail(errors.UNKNOWN_PAGE, "The field names a page this draft does not have", op.id)
    target = op.name if op.kind != "field.add" else None
    if target is not None and target in removed_names and not form.of(target):
        # The field was on a page this draft deletes: it went with the page.
        warnings.append({"code": "op_on_removed_page", "op_id": op.id})
        return False

    if op.kind == "field.add":
        _check_rect(doc, index, op.spec.rect, op.id)
        _create(form, op.id, index, op.spec)
        touched.add(index)
        regions.append({"page_uid": op.page_uid, "rect": rect_dict(op.spec.rect)})
        return True

    if op.kind == "field.remove":
        widgets = _find(form, op.name, op.export_value, op.id)
        for w in widgets:
            _remove_widget(doc, w)
            regions.append({"page_uid": form.uid_by_index.get(w.index), "rect": rect_dict(w.rect)})
        form.refresh()
        return True

    if op.kind == "field.update":
        spec = op.spec
        current = op.name or spec.name
        widgets = _find(form, current, op.export_value, op.id)
        if widgets[0].type == "radio" and op.export_value is None and len(widgets) > 1:
            raise _fail(errors.UNKNOWN_FIELD, "Name the radio button to change (export_value)", op.id)
        _check_rect(doc, index, spec.rect, op.id)
        if spec.name != current and form.of(spec.name):
            others = form.of(spec.name)
            if not (spec.type == "radio" and all(w.type == "radio" for w in others)):
                raise _fail(errors.FIELD_NAME_TAKEN, "A field with that name exists", op.id)
        old = widgets[0]
        if old.type == "radio":
            selected = old.value == old.export_value
            keep = spec.export_value if selected and spec.type == "radio" else None
        else:
            keep = _compatible(old.value, spec)
        for w in widgets:
            _remove_widget(doc, w)
            regions.append({"page_uid": form.uid_by_index.get(w.index), "rect": rect_dict(w.rect)})
            touched.add(w.index)
        form.refresh()
        _create(form, op.id, index, spec, value=keep)
        touched.add(index)
        regions.append({"page_uid": op.page_uid, "rect": rect_dict(spec.rect)})
        return True

    if op.kind == "field.value":
        widgets = _find(form, op.name, None, op.id)
        value = check_value(widgets, op.value, op.id)
        _write_value(form, widgets, value)
        for w in widgets:
            regions.append({"page_uid": form.uid_by_index.get(w.index), "rect": rect_dict(w.rect)})
        return True
    raise ValueError(op.kind)  # pragma: no cover


def _tab_order(doc: adapter.Doc, index: int) -> None:
    """`/Annots`: the other annotations first (as they were), then the
    widgets by `tab_index` (fields without one keep their place, last);
    `/Tabs /S` on the page."""
    page = doc.page_xref(index)
    annots, holder = _refs(doc, page, "Annots")
    widgets = {raw["xref"] for raw in doc.widgets(index)}
    tabs: dict[int, float] = {}
    for xref in widgets:
        kind, value = doc.get_key(xref, SWM_TAB)
        tabs[xref] = float(value) if kind in ("int", "float") else float("inf")
    position = {xref: n for n, xref in enumerate(annots)}
    others = [a for a in annots if a not in widgets]
    ordered = sorted((a for a in annots if a in widgets), key=lambda a: (tabs[a], position[a]))
    if others + ordered != annots:
        _set_refs(doc, page, "Annots", others + ordered, holder)
    doc.set_key(page, "Tabs", "/S")
    doc.reload_page(index)


def prune_orphan_fields(doc: adapter.Doc) -> int:
    """After pages were deleted: AcroForm fields none of whose widgets is on
    a page any more are dropped from `/Fields`. Returns how many."""
    refs, holder = _acroform_fields(doc)
    if not refs:
        return 0
    on_pages: set[int] = set()
    for index in range(doc.page_count):
        on_pages.update(_refs(doc, doc.page_xref(index), "Annots")[0])

    def alive(node: int, depth: int = 0) -> bool:
        if node in on_pages:
            return True
        if depth > 16:
            return True  # a malformed (cyclic) tree: leave it alone
        kids, _holder = _refs(doc, node, "Kids")
        return any(alive(kid, depth + 1) for kid in kids)

    keep = [r for r in refs if alive(r)]
    if len(keep) != len(refs):
        _set_acroform_fields(doc, keep, holder)
    return len(refs) - len(keep)


def names_on_pages(doc: adapter.Doc, indices) -> set[str]:
    return {raw["name"] for index in indices for raw in doc.widgets(index)}


# -- flatten -------------------------------------------------------------------------


def has_visible_widgets(pdf_bytes: bytes) -> bool:
    with adapter.Doc(pdf_bytes) as doc:
        return doc.has_visible_widgets()


def flatten(pdf_bytes: bytes) -> bytes:
    """Every widget -> page content (its current appearance), no widget and no
    AcroForm left. A PDF without widgets comes back unchanged (same bytes)."""
    with adapter.Doc(pdf_bytes) as doc:
        if doc.widget_count() == 0:
            return pdf_bytes
        doc.bake_widgets()
        return doc.save(subset_fonts=False)


# -- summary for the audit trail (no values) ----------------------------------------


def op_stats(ops: list[FieldOp]) -> dict:
    """Counts only -- never names, never values (PII)."""
    by_kind: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for op in ops:
        by_kind[op.kind] = by_kind.get(op.kind, 0) + 1
        if op.spec is not None:
            by_type[op.spec.type] = by_type.get(op.spec.type, 0) + 1
    return {"total": len(ops), "by_kind": dict(sorted(by_kind.items())), "by_type": dict(sorted(by_type.items()))}


def summary(pdf_bytes: bytes) -> dict:
    """How many fields of each type a version holds (no names, no values)."""
    with adapter.Doc(pdf_bytes) as doc:
        widgets = read_widgets(doc)
    by_type: dict[str, int] = {}
    for w in widgets:
        by_type[w.type] = by_type.get(w.type, 0) + 1
    return {"widgets": len(widgets), "fields": len({w.name for w in widgets}), "by_type": dict(sorted(by_type.items()))}


__all__ = [
    "FieldOp",
    "FieldSpec",
    "apply_field_ops",
    "field_dicts",
    "flatten",
    "has_visible_widgets",
    "op_stats",
    "prune_orphan_fields",
    "summary",
]

