import { useCallback, useEffect, useRef, useState } from 'react';
import { assetsApi, type AssetOut } from '../../api/document';
import { tokenStore } from '../../api/client';
import { userSignaturesApi } from '../../api/userSignatures';
import { checkImageFile } from '../../lib/imageFile';
import { ANNOTATION_COLORS, ANNOTATION_COLOR_HEX, ANNOTATION_COLOR_LABEL } from '../../lib/annotations';
import { STAMP_PRESETS, STAMP_DATE_MAX, STAMP_TEXT_MAX } from '../../lib/annotationKinds/v7';
import type { UserSignature } from '../../types/contract';
import type { StampSpec } from '../tools/types';

/**
 * F2: what the drop-downs hold besides their tools (EDITOR-V2-PLAN §7.4):
 *
 *   Images ▾   the gallery's thumbnails (click: the next click on the page
 *              places a COPY of it -- deleting it from the gallery later does
 *              not touch what was placed), «Add to gallery…», «New Stamp…»;
 *   Sign ▾     the saved signatures' thumbnails (click: the next click on the
 *              page places it), «New Signature…», «My signatures…»;
 *   Annotate ▾ «Hide annotations» / «Show annotations» -- ON SCREEN ONLY: the
 *              export dialog still counts (and includes) the hidden ones.
 */

/* -- the gallery ----------------------------------------------------------------------- */

export function useGallery(enabled: boolean) {
  const [assets, setAssets] = useState<AssetOut[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const reload = useCallback(async () => {
    try {
      const list = await assetsApi.list();
      setAssets(Array.isArray(list) ? list : []);
      setError(null);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'The gallery could not be loaded');
      setAssets([]);
    }
  }, []);
  useEffect(() => {
    if (enabled) void reload();
  }, [enabled, reload]);
  const add = useCallback(
    async (file: File, kind: 'image' | 'stamp' = 'image') => {
      const checked = await checkImageFile(file);
      if (!checked.ok) {
        setError(checked.error);
        return null;
      }
      try {
        const made = await assetsApi.add({ image_data: checked.dataUrl, kind, label: file.name.slice(0, 80) });
        await reload();
        return made;
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : 'The picture could not be added');
        return null;
      }
    },
    [reload],
  );
  const remove = useCallback(
    async (assetId: string) => {
      try {
        await assetsApi.remove(assetId);
      } finally {
        await reload();
      }
    },
    [reload],
  );
  return { assets, error, reload, add, remove };
}

export type Gallery = ReturnType<typeof useGallery>;

/** An authorised picture (the gallery route needs the bearer token). */
function AuthImage({ url, alt }: { url: string; alt: string }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    let made: string | null = null;
    const token = tokenStore.get();
    fetch(url, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
      .then((response) => (response.ok ? response.blob() : null))
      .then((blob) => {
        if (!blob || !live) return;
        made = URL.createObjectURL(blob);
        setSrc(made);
      })
      .catch(() => undefined);
    return () => {
      live = false;
      if (made) URL.revokeObjectURL(made);
    };
  }, [url]);
  return src ? <img src={src} alt={alt} /> : <span className="ws-thumb__empty" aria-hidden="true" />;
}

export function ImagesExtras({
  gallery,
  close,
  onPick,
  onNewStamp,
}: {
  gallery: Gallery;
  close: () => void;
  onPick: (asset: AssetOut) => void;
  onNewStamp: () => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const assets = gallery.assets ?? [];
  return (
    <>
      <span className="ws-toolmenu__section" role="presentation" aria-hidden="true">
        Gallery
      </span>
      <span className="ws-thumbs" data-testid="gallery-thumbs" data-count={assets.length}>
        {assets.map((asset) => (
          <span key={asset.asset_id} className="ws-thumb">
            <button
              type="button"
              className="ws-thumb__pick"
              data-testid={`gallery-${asset.asset_id}`}
              aria-label={`Place ${asset.label ?? 'this picture'} (click the page next)`}
              title={`${asset.label ?? 'Picture'} — click, then click the page to place a copy`}
              onClick={() => {
                close();
                onPick(asset);
              }}
            >
              <AuthImage url={assetsApi.imageUrl(asset.asset_id)} alt="" />
            </button>
            <button
              type="button"
              className="ws-thumb__remove"
              data-testid={`gallery-remove-${asset.asset_id}`}
              aria-label={`Remove ${asset.label ?? 'this picture'} from the gallery`}
              title="Remove from the gallery (pictures already placed stay)"
              onClick={() => void gallery.remove(asset.asset_id)}
            >
              ×
            </button>
          </span>
        ))}
        {assets.length === 0 && <span className="ws-thumbs__empty">No pictures in your gallery yet.</span>}
      </span>
      {gallery.error && (
        <span className="ws-toolmenu__error" role="alert" data-testid="gallery-error">
          {gallery.error}
        </span>
      )}
      <button
        type="button"
        className="ws-toolmenu__item"
        data-testid="menu-gallery-add"
        aria-label="Add a picture to the gallery"
        title="Add a PNG or JPEG (up to 2 MB) to your gallery"
        onClick={() => fileRef.current?.click()}
      >
        <span className="editor__tool-glyph" aria-hidden="true">＋</span>
        <span className="ws-toolmenu__label">Add to gallery…</span>
      </button>
      <input
        ref={fileRef}
        type="file"
        accept="image/png,image/jpeg"
        hidden
        data-testid="gallery-file"
        aria-label="Picture for the gallery"
        onChange={(event) => {
          const file = event.target.files?.[0];
          event.target.value = '';
          if (file) void gallery.add(file);
        }}
      />
      <button
        type="button"
        className="ws-toolmenu__item"
        data-testid="menu-new-stamp"
        aria-label="New stamp"
        title="Make a stamp: a word or a short text, colour, border, and a date line only if you type one"
        onClick={() => {
          close();
          onNewStamp();
        }}
      >
        <span className="editor__tool-glyph" aria-hidden="true">＋</span>
        <span className="ws-toolmenu__label">New Stamp…</span>
      </button>
    </>
  );
}

/** A stored signature's picture (ownership-checked route, as in the dialog). */
function StoredThumb({ signature }: { signature: UserSignature }) {
  const [src, setSrc] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    userSignaturesApi
      .imageAsDataUrl(signature.id)
      .then((url) => live && setSrc(url))
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [signature.id]);
  return src ? <img src={src} alt="" /> : <span className="ws-thumb__empty" aria-hidden="true" />;
}

export function SignExtras({
  library,
  blockedReason,
  close,
  onPick,
  onNew,
  onManage,
}: {
  library: UserSignature[];
  blockedReason: string | null;
  close: () => void;
  onPick: (signature: UserSignature) => void;
  onNew: () => void;
  onManage: () => void;
}) {
  return (
    <>
      <span className="ws-toolmenu__section" role="presentation" aria-hidden="true">
        My signatures
      </span>
      <span className="ws-thumbs" data-testid="sign-thumbs" data-count={library.length}>
        {library.map((signature) => (
          <span key={signature.id} className="ws-thumb ws-thumb--sig">
            <button
              type="button"
              className="ws-thumb__pick"
              data-testid={`sign-saved-${signature.id}`}
              disabled={Boolean(blockedReason)}
              aria-label={`Place ${signature.label ?? 'this signature'} (click the page next)`}
              title={blockedReason ?? `${signature.label ?? 'Saved signature'} — click, then click the page`}
              onClick={() => {
                close();
                onPick(signature);
              }}
            >
              <StoredThumb signature={signature} />
            </button>
          </span>
        ))}
        {library.length === 0 && <span className="ws-thumbs__empty">No saved signatures yet.</span>}
      </span>
      <button
        type="button"
        className="ws-toolmenu__item"
        data-testid="menu-new-signature"
        disabled={Boolean(blockedReason)}
        aria-label="New signature"
        title={blockedReason ?? 'Draw, upload or type a signature, then click the page to place it'}
        onClick={() => {
          close();
          onNew();
        }}
      >
        <span className="editor__tool-glyph" aria-hidden="true">＋</span>
        <span className="ws-toolmenu__label">New Signature…</span>
      </button>
      <button
        type="button"
        className="ws-toolmenu__item"
        data-testid="menu-my-signatures"
        aria-label="My signatures"
        title="Manage your saved signatures (opens the My signatures page)"
        onClick={() => {
          close();
          onManage();
        }}
      >
        <span className="editor__tool-glyph" aria-hidden="true">☰</span>
        <span className="ws-toolmenu__label">My signatures…</span>
      </button>
    </>
  );
}

export function AnnotateExtras({
  hidden,
  count,
  close,
  onToggle,
}: {
  hidden: boolean;
  count: number;
  close: () => void;
  onToggle: () => void;
}) {
  return (
    <>
      <span className="ws-toolmenu__section" role="presentation" aria-hidden="true">
        View
      </span>
      <button
        type="button"
        className="ws-toolmenu__item"
        data-testid="menu-toggle-annotations"
        data-hidden={hidden ? 'true' : 'false'}
        aria-pressed={hidden}
        aria-label={hidden ? 'Show annotations' : 'Hide annotations'}
        title="On screen only: a download still includes every annotation"
        onClick={() => {
          close();
          onToggle();
        }}
      >
        <span className="editor__tool-glyph" aria-hidden="true">{hidden ? '◉' : '◌'}</span>
        <span className="ws-toolmenu__label">
          {hidden ? `Show annotations (${count})` : 'Hide annotations'}
        </span>
      </button>
    </>
  );
}

/* -- «New Stamp…» ------------------------------------------------------------------------ */

export function StampDialog({
  initial,
  onSave,
  onPicture,
  onClose,
}: {
  initial: StampSpec;
  onSave: (spec: StampSpec) => void;
  /** «A picture instead»: the file goes to the gallery (kind `stamp`) and is placed like one. */
  onPicture: (file: File) => void;
  onClose: () => void;
}) {
  const [mode, setMode] = useState<'preset' | 'text'>(initial.text ? 'text' : 'preset');
  const [preset, setPreset] = useState(initial.preset ?? 'approved');
  const [text, setText] = useState(initial.text ?? '');
  // The date line starts EMPTY and stays so unless typed (2026-09-25: no «now»).
  const [date, setDate] = useState('');
  const [color, setColor] = useState(initial.color);
  const [border, setBorder] = useState(initial.border);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const save = () => {
    if (mode === 'text' && !text.trim()) {
      setError('Type the stamp’s text, or pick a ready word.');
      return;
    }
    onSave({
      preset: mode === 'preset' ? preset : null,
      text: mode === 'text' ? text.trim().slice(0, STAMP_TEXT_MAX) : null,
      date_text: date.trim() ? date.trim().slice(0, STAMP_DATE_MAX) : null,
      color,
      border,
    });
  };

  return (
    <div
      className="modal"
      role="dialog"
      aria-modal="true"
      aria-label="New stamp"
      data-testid="stamp-dialog"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === 'Escape') onClose();
      }}
    >
      <div className="modal__panel ws-dialog">
        <h2 className="modal__title">New stamp</h2>
        <p className="modal__hint">Then click the page to place it. The date line is only printed if you type one.</p>
        <div className="ws-dialog__choices" role="radiogroup" aria-label="Stamp label">
          <label>
            <input type="radio" name="stamp-mode" data-testid="stamp-mode-preset" checked={mode === 'preset'} onChange={() => setMode('preset')} />{' '}
            A ready word
          </label>
          <label>
            <input type="radio" name="stamp-mode" data-testid="stamp-mode-text" checked={mode === 'text'} onChange={() => setMode('text')} />{' '}
            My own text
          </label>
        </div>
        {mode === 'preset' ? (
          <select className="ws-dialog__input" data-testid="stamp-preset" aria-label="Word" value={preset} onChange={(event) => setPreset(event.target.value)}>
            {STAMP_PRESETS.map((name) => (
              <option key={name} value={name}>
                {name.toUpperCase()}
              </option>
            ))}
          </select>
        ) : (
          <input
            className="ws-dialog__input"
            data-testid="stamp-text"
            aria-label="Stamp text"
            maxLength={STAMP_TEXT_MAX}
            value={text}
            autoFocus
            onChange={(event) => {
              setText(event.target.value);
              setError(null);
            }}
          />
        )}
        <label className="field">
          <span>Date line (optional — typed, never filled in for you)</span>
          <input
            className="ws-dialog__input"
            data-testid="stamp-date"
            aria-label="Date line"
            placeholder="e.g. 09/30/2026"
            maxLength={STAMP_DATE_MAX}
            value={date}
            onChange={(event) => setDate(event.target.value)}
          />
        </label>
        <div className="ws-dialog__row" role="group" aria-label="Colour">
          {ANNOTATION_COLORS.map((option) => (
            <button
              key={option}
              type="button"
              className={`ws-color__option${option === color ? ' is-selected' : ''}`}
              data-testid={`stamp-color-${option}`}
              aria-label={ANNOTATION_COLOR_LABEL[option]}
              aria-pressed={option === color}
              title={ANNOTATION_COLOR_LABEL[option]}
              style={{ background: ANNOTATION_COLOR_HEX[option] }}
              onClick={() => setColor(option)}
            />
          ))}
          <label className="checkbox">
            <input type="checkbox" data-testid="stamp-border" checked={border} onChange={(event) => setBorder(event.target.checked)} />
            Border
          </label>
        </div>
        {error && (
          <p className="alert alert--error" role="alert" data-testid="stamp-error">
            {error}
          </p>
        )}
        <div className="modal__actions">
          <button type="button" className="button button--link" data-testid="stamp-picture" onClick={() => fileRef.current?.click()}>
            A picture instead…
          </button>
          <input
            ref={fileRef}
            type="file"
            hidden
            accept="image/png,image/jpeg"
            data-testid="stamp-picture-file"
            aria-label="Stamp picture"
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = '';
              if (file) onPicture(file);
            }}
          />
          <span className="modal__spacer" />
          <button type="button" className="button" data-testid="stamp-cancel" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="button button--primary" data-testid="stamp-save" onClick={save}>
            Use this stamp
          </button>
        </div>
      </div>
    </div>
  );
}
