import { useCallback, useEffect, useRef, useState } from 'react';

export interface SignaturePadState {
  canvasRef: React.RefObject<HTMLCanvasElement>;
  isEmpty: boolean;
  clear: () => void;
  /** PNG data URL with a transparent background, ready to POST. */
  toDataUrl: () => string | null;
}

/**
 * Pointer-driven drawing on a canvas. Kept transparent so the signature
 * overlays the contract's ruled line rather than covering it with a white box.
 */
export function useSignature(width = 480, height = 160): SignaturePadState {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const drawingRef = useRef(false);
  const lastPointRef = useRef<{ x: number; y: number } | null>(null);
  const [isEmpty, setIsEmpty] = useState(true);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ratio = window.devicePixelRatio || 1;
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;

    const context = canvas.getContext('2d');
    if (!context) return;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.lineWidth = 2.2;
    context.lineCap = 'round';
    context.lineJoin = 'round';
    context.strokeStyle = '#111827';

    const position = (event: PointerEvent) => {
      const rect = canvas.getBoundingClientRect();
      return { x: event.clientX - rect.left, y: event.clientY - rect.top };
    };

    const start = (event: PointerEvent) => {
      event.preventDefault();
      canvas.setPointerCapture(event.pointerId);
      drawingRef.current = true;
      lastPointRef.current = position(event);
    };

    const move = (event: PointerEvent) => {
      if (!drawingRef.current) return;
      const point = position(event);
      const last = lastPointRef.current;
      if (last) {
        context.beginPath();
        context.moveTo(last.x, last.y);
        context.lineTo(point.x, point.y);
        context.stroke();
      }
      lastPointRef.current = point;
      setIsEmpty(false);
    };

    const end = (event: PointerEvent) => {
      drawingRef.current = false;
      lastPointRef.current = null;
      if (canvas.hasPointerCapture(event.pointerId)) {
        canvas.releasePointerCapture(event.pointerId);
      }
    };

    canvas.addEventListener('pointerdown', start);
    canvas.addEventListener('pointermove', move);
    canvas.addEventListener('pointerup', end);
    canvas.addEventListener('pointerleave', end);
    canvas.addEventListener('pointercancel', end);

    return () => {
      canvas.removeEventListener('pointerdown', start);
      canvas.removeEventListener('pointermove', move);
      canvas.removeEventListener('pointerup', end);
      canvas.removeEventListener('pointerleave', end);
      canvas.removeEventListener('pointercancel', end);
    };
  }, [width, height]);

  const clear = useCallback(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext('2d');
    if (!canvas || !context) return;
    context.save();
    context.setTransform(1, 0, 0, 1, 0, 0);
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.restore();
    setIsEmpty(true);
  }, []);

  const toDataUrl = useCallback(() => {
    if (isEmpty) return null;
    return canvasRef.current?.toDataURL('image/png') ?? null;
  }, [isEmpty]);

  return { canvasRef, isEmpty, clear, toDataUrl };
}
