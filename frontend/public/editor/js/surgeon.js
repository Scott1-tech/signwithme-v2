    class SejdaBinaryStreamSurgeon {
      /**
       * Sahifaning barcha siqilgan binar /Contents oqimlarini ochib (inflate),
       * yagona raw PDF operator satriga aylantiradi.
       */
      static extractRawPageStreamString(pdfDoc, page) {
        const { PDFName, PDFRawStream, PDFArray, decodePDFRawStream } = PDFLib;
        const contentsRef = page.node.get(PDFName.of('Contents'));
        if (!contentsRef) return '';

        const contentsObj = pdfDoc.context.lookup(contentsRef);
        const chunks = [];

        const decodeOne = (streamObj) => {
          if (streamObj instanceof PDFRawStream) {
            const bytes = decodePDFRawStream(streamObj).decode();
            let s = '';
            for (let i = 0; i < bytes.length; i++) {
              s += String.fromCharCode(bytes[i]);
            }
            chunks.push(s);
          }
        };

        if (contentsObj instanceof PDFRawStream) {
          decodeOne(contentsObj);
        } else if (contentsObj instanceof PDFArray) {
          const arr = contentsObj.asArray();
          for (let i = 0; i < arr.length; i++) {
            const sub = pdfDoc.context.lookup(arr[i]);
            decodeOne(sub);
          }
        }

        return chunks.join('\n');
      }

      /**
       * Raw PDF Content-Stream ichidan:
       * 1) Barcha matn ko'rsatish operatorlarini (BT ... ET ichidagi Tj, TJ, ', ")
       * 2) Barcha gorizontal chiziq operatorlarini (x1 y1 m x2 y2 l S va ingichka x y w h re f/S)
       * binar darajada tozalaydi, lekin logotiplar, rasmlar (Do) va murakkab shakllarni saqlab qoladi!
       */
      static stripTextAndHorizontalLinesFromStream(rawStream) {
        // 1. BT ... ET bloklari ichidagi matn chiqarish operatorlarini (Tj, TJ) tozalash,
        // lekin ichidagi rang/grafik holat (rg, RG, g, G, k, K) operatorlarini saqlab qolish:
        let cleaned = rawStream.replace(/BT[\s\S]*?ET/g, (btBlock) => {
          const keptStateLines = [];
          const lines = btBlock.split(/\r?\n/);
          for (const ln of lines) {
            const t = ln.trim();
            if (/\b(rg|RG|g|G|k|K|cs|CS|sc|SC|scn|SCN|gs)\s*$/.test(t)) {
              keptStateLines.push(t);
            }
          }
          return keptStateLines.join('\n');
        });

        // 2. Binar oqimdagi gorizontal chiziqlarni (x1 y1 m x2 y2 l S / s / B / f) tozalash:
        // Oddiy gorizontal moveTo + lineTo + stroke:
        cleaned = cleaned.replace(
          /(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+m\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+l\s+(?:h\s+)?[SsfFBb\*]+/g,
          (match, x1, y1, x2, y2) => {
            const dx = Math.abs(parseFloat(x2) - parseFloat(x1));
            const dy = Math.abs(parseFloat(y2) - parseFloat(y1));
            if (dx >= 15 && dy <= 3.0) {
              return ''; // Gorizontal chiziqni binar oqimdan olib tashlaymiz!
            }
            return match;
          }
        );

        // 3. Ingichka to'rtburchak sifatida chizilgan chiziqlar (x y w h re f / S) va kichik Checkbox kvadratlarni tozalash:
        cleaned = cleaned.replace(
          /(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+re\s+(?:h\s+)?[SsfFBb\*]+/g,
          (match, x, y, w, h) => {
            const width = Math.abs(parseFloat(w));
            const height = Math.abs(parseFloat(h));
            // Gorizontal chiziq (kengligi >= 15pt, balandligi <= 3.5pt)
            if (width >= 15 && height <= 3.5) {
              return '';
            }
            // Checkbox kvadrati (6pt <= w <= 16pt)
            if (width >= 6 && width <= 16 && Math.abs(width - height) <= 2.5) {
              return '';
            }
            return match;
          }
        );

        return cleaned;
      }

      /**
       * Tozalangan binar satrni qayta FlateDecode oqimiga siqib, asl PDF sahifasiga yozadi.
       */
      static writeCleanedStreamToPage(pdfDoc, page, cleanedStreamStr) {
        const { PDFName } = PDFLib;
        const bytes = new Uint8Array(cleanedStreamStr.length);
        for (let i = 0; i < cleanedStreamStr.length; i++) {
          bytes[i] = cleanedStreamStr.charCodeAt(i) & 0xff;
        }
        const newFlateStream = pdfDoc.context.flateStream(bytes);
        const streamRef = pdfDoc.context.register(newFlateStream);
        page.node.set(PDFName.of('Contents'), streamRef);
      }
    }
