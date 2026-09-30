import { SignatureModal, type TypedFont } from './SignatureModal';
import type { TypedSignatureFont } from '../api/document';
import type { SignatureChooser } from '../hooks/useSignatureChooser';
import type { UserSignature } from '../types/contract';

interface Props {
  chooser: SignatureChooser;
  title: string;
  library: UserSignature[];
  libraryLoaded: boolean;
  busy: boolean;
  /** F2: the Type tab (only with B2). */
  typedFonts?: TypedFont[] | null;
  onTyped?: (text: string, font: TypedSignatureFont) => void;
}

/** The one signature dialog of a page, shown while `chooser` has a request open. */
export function SignatureChooserHost({ chooser, title, library, libraryLoaded, busy, typedFonts, onTyped }: Props) {
  if (!chooser.request) return null;
  return (
    <SignatureModal
      title={title}
      library={library}
      libraryLoaded={libraryLoaded}
      busy={busy}
      onClose={chooser.close}
      onChooseStored={(signature) => void chooser.chooseStored(signature)}
      onConfirm={(dataUrl, previewUrl) => void chooser.confirm(dataUrl, previewUrl)}
      saveToLibrary={chooser.saveToLibrary}
      onSaveToLibraryChange={chooser.setSaveToLibrary}
      forInitials={chooser.request.purpose === 'initials'}
      typedFonts={typedFonts}
      onTyped={onTyped}
    />
  );
}
